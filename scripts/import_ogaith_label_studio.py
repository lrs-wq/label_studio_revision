"""Run on the Label Studio server to import the prepared bbox-only dataset.

Uses the running application's API and existing local authentication. Tokens
remain in server memory. Safe resumption is scoped to the recorded new project.
"""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

import requests


DATASET = 'dog_bbox_ogaith_20260909'
BUNDLE = Path('/data/label_studio/preannotations') / DATASET
ASSETS = Path('/data/label_studio/local_files') / DATASET
DB = Path('/root/label_studio/label_studio_data/label_studio.sqlite3')
PUBLIC = 'https://ai.hzzootech.com'
API = 'http://127.0.0.1:8090'
RECEIPT = BUNDLE / 'import_receipt.json'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def read_db():
    return sqlite3.connect(f'file:{DB}?mode=ro', uri=True)


def main():
    manifest = json.loads((BUNDLE / 'manifest.json').read_text())
    assert digest(BUNDLE / 'tasks.json') == manifest['tasks_sha256']
    assert digest(BUNDLE / 'label_config.xml') == manifest['config_sha256']
    tasks = json.loads((BUNDLE / 'tasks.json').read_text())
    config = (BUNDLE / 'label_config.xml').read_text()
    tags = {node.tag for node in ElementTree.fromstring(config).iter()}
    assert tags <= {'View', 'Header', 'Image', 'RectangleLabels', 'Label'}
    assert len(tasks) == manifest['image_count'] == 1267
    assert sum(len(t['predictions'][0]['result']) for t in tasks) == 1587
    for entry in manifest['images']:
        image = ASSETS / entry['path']
        assert image.is_relative_to(ASSETS) and image.stat().st_size == entry['size']
        assert digest(image) == entry['sha256'], entry['path']
    print(json.dumps({'phase': 'images_verified', 'count': len(tasks)}), flush=True)

    with read_db() as db:
        token, user_id = db.execute(
            'SELECT t.key,u.id FROM authtoken_token t JOIN htx_user u ON u.id=t.user_id '
            'WHERE u.is_active=1 ORDER BY u.is_superuser DESC,u.id LIMIT 1'
        ).fetchone()
    session = requests.Session()
    session.headers.update({'Authorization': 'Token ' + token, 'Host': 'ai.hzzootech.com'})

    def api(method, path, **kwargs):
        response = session.request(method, API + path, timeout=120, **kwargs)
        if not response.ok:
            # Do not print authentication headers or unfiltered project objects.
            raise RuntimeError(f'{method} {path}: HTTP {response.status_code}; {response.text[:1500]}')
        return response.json()

    receipt = json.loads(RECEIPT.read_text()) if RECEIPT.exists() else {}
    if receipt:
        assert receipt['tasks_sha256'] == manifest['tasks_sha256']
        project = api('GET', f"/api/projects/{receipt['project_id']}/")
        assert project['title'] == manifest['project_title'] and project['label_config'] == config
    else:
        existing = api('GET', '/api/projects/', params={'page_size': 100})
        assert not any(p['title'] == manifest['project_title'] for p in existing['results']), 'Project title already exists without this receipt'
        backup_dir = Path('/data/label_studio/backups') / DATASET
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / ('before_import_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.sqlite3')
        if backup.exists():
            raise FileExistsError(backup)
        with read_db() as source, sqlite3.connect(backup) as target:
            source.backup(target)
        project = api('POST', '/api/projects/', json={
            'title': manifest['project_title'],
            'description': '1,267 张图片，ogaith 狗框预标注；仅核对目标检测框。496 张有优先复核提示，8 张未检出需确认。',
            'label_config': config,
            'expert_instruction': '<h3>狗目标检测框核对</h3><p>仅标注 dog 矩形框。调整框使其贴合狗的完整可见范围；删除食盆、垫子误框及同一只狗的重复框，补充漏框。所有候选均为 ogaith 预标注，修改并提交后才算人工核对完成。</p><p>未检出不代表没有狗。请确认画面确实无狗后再提交空标注。可按“review_priority”筛选优先核对图片。</p>',
            'show_instruction': False,
            'show_collab_predictions': True,
            'model_version': 'ogaith_dog_bbox_20260909',
            'enable_empty_annotation': True,
            'maximum_annotations': 1,
            'sampling': 'Sequential sampling',
            'is_published': True,
            'is_draft': False,
            'evaluate_predictions_automatically': False,
            'color': '#20B981',
        })
        receipt = {'project_id': project['id'], 'project_title': project['title'],
                   'tasks_sha256': manifest['tasks_sha256'], 'database_backup': str(backup),
                   'created_by_user_id': user_id, 'status': 'importing'}
        save(RECEIPT, receipt)
    project_id = project['id']
    print(json.dumps({'phase': 'project_ready', 'project_id': project_id}, ensure_ascii=False), flush=True)

    if 'storage_id' not in receipt:
        storages = api('GET', '/api/storages/localfiles/', params={'project': project_id})
        matching = [s for s in storages if s['path'] == str(ASSETS)]
        assert len(matching) <= 1
        storage = matching[0] if matching else api('POST', '/api/storages/localfiles/', json={
            'project': project_id, 'title': 'ogaith 狗框核对图片', 'path': str(ASSETS),
            # Authorize image serving only. Tasks already carry their predictions
            # through JSON import; later storage sync must not duplicate them.
            'regex_filter': '(?!)', 'use_blob_urls': True,
            'description': '本批图片已连同检测框完整导入。此连接用于图片访问，不另行同步生成任务。',
        })
        receipt['storage_id'] = storage['id']
        save(RECEIPT, receipt)

    with read_db() as db:
        existing_rows = db.execute('SELECT id,data FROM task WHERE project_id=?', (project_id,)).fetchall()
    existing_hashes = {json.loads(data)['source_sha256'] for _, data in existing_rows}
    expected_hashes = {t['data']['source_sha256'] for t in tasks}
    assert len(existing_hashes) == len(existing_rows) and existing_hashes <= expected_hashes
    remaining = [t for t in tasks if t['data']['source_sha256'] not in existing_hashes]
    for start in range(0, len(remaining), 100):
        batch = remaining[start:start + 100]
        result = api('POST', f'/api/projects/{project_id}/import',
                     params={'return_task_ids': 'true'}, json=batch)
        assert result['task_count'] == len(batch) and result['annotation_count'] == 0
        assert result['prediction_count'] == len(batch)
        receipt['imported_tasks'] = len(existing_rows) + start + len(batch)
        save(RECEIPT, receipt)
        print(json.dumps({'phase': 'importing', 'completed': receipt['imported_tasks'], 'total': len(tasks)}), flush=True)

    view_response = api('GET', '/api/dm/views/', params={'project': project_id})
    views = view_response.get('results', []) if isinstance(view_response, dict) else view_response
    view_specs = [
        ('全部图片', []),
        ('优先核对', [{'filter': 'filter:tasks:data.review_priority', 'operator': 'equal',
                     'type': 'String', 'value': '优先核对'}]),
        ('模型未检出', [{'filter': 'filter:tasks:data.candidate_box_count', 'operator': 'equal',
                       'type': 'Number', 'value': 0}]),
    ]
    receipt['views'] = {}
    for title, filters in view_specs:
        matches = [v for v in views if v['data'].get('title') == title]
        assert len(matches) <= 1
        view = matches[0] if matches else api('POST', '/api/dm/views/', json={
            'project': project_id,
            'data': {'title': title, 'type': 'list', 'ordering': [],
                     'filters': {'conjunction': 'and', 'items': filters}},
        })
        receipt['views'][title] = view['id']
    save(RECEIPT, receipt)

    with read_db() as db:
        rows = db.execute('SELECT id,data,is_labeled FROM task WHERE project_id=? ORDER BY id', (project_id,)).fetchall()
        predictions = db.execute('SELECT task_id,result,model_version FROM prediction WHERE project_id=?', (project_id,)).fetchall()
        annotations = db.execute('SELECT count(*) FROM task_completion WHERE project_id=?', (project_id,)).fetchone()[0]
    assert len(rows) == len(tasks) and len(predictions) == len(tasks)
    by_hash = {t['data']['source_sha256']: t for t in tasks}
    task_map = {task_id: json.loads(data) for task_id, data, _ in rows}
    assert len({data['source_sha256'] for data in task_map.values()}) == len(tasks)
    result_types = set()
    for task_id, raw, version in predictions:
        expected = by_hash[task_map[task_id]['source_sha256']]
        actual = json.loads(raw)
        assert actual == expected['predictions'][0]['result']
        assert version == 'ogaith_dog_bbox_20260909'
        result_types.update(r['type'] for r in actual)
    assert result_types == {'rectanglelabels'}

    for task_id, data, _ in [rows[0], rows[len(rows) // 2], rows[-1]]:
        task = api('GET', f'/api/tasks/{task_id}/')
        assert task['project'] == project_id and len(task['predictions']) == 1
        response = session.get(API + json.loads(data)['image'], timeout=30)
        assert response.status_code == 200 and response.headers['Content-Type'].startswith('image/')
        assert hashlib.sha256(response.content).hexdigest() == json.loads(data)['source_sha256']

    receipt.update({'status': 'completed', 'project_url': f'{PUBLIC}/projects/{project_id}/data/',
                    'image_count': len(rows), 'prediction_count': len(predictions), 'box_count': 1587,
                    'keypoint_count': 0, 'human_annotation_count_at_verification': annotations,
                    'image_sha256_verified': len(tasks), 'result_types': sorted(result_types),
                    'priority_review_count': 496, 'no_detection_count': 8,
                    'image_root': str(ASSETS), 'bundle_root': str(BUNDLE),
                    'sample_image_http_checks': 3, 'verified_at': datetime.now(timezone.utc).isoformat()})
    save(RECEIPT, receipt)
    save(BUNDLE / 'task_mapping.json', [{'task_id': task_id, **json.loads(data)} for task_id, data, _ in rows])
    print(json.dumps(receipt, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
