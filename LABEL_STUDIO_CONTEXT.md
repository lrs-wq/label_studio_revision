# Label Studio Context

Last verified: 2026-06-15, Asia/Shanghai

This document summarizes the current Label Studio + YOLO preannotation setup in
`D:\gogelila\label_studio`, plus the fixes and operational notes from the recent
debugging sessions.

## Current Status

- Label Studio is running as Windows service `LabelStudio`.
- Label Studio URL: `http://127.0.0.1:8090`
- Service status at last check: `Running`
- Service startup type: `Automatic`
- YOLO preannotation backend is running in Docker on port `9090`.
- YOLO backend health at last check:
  - `status`: `UP`
  - `model_version`: `cat_feeding_head_v7_yolo26s_tune2_200e_b32_wandb`
  - `model_path`: `/models/cat_feeding_head_v7_yolo26s_tune2_200e_b32_wandb.pt`
  - labels: `0=cat`, `1=feeding_head`
- Project `5` / `v1_cat` has been refreshed with v7 preannotations.
- Project `4` still has its existing v6 prediction rows; it was not reset during
  the v7 `v1_cat` refresh.
- Project `7` / `v9_yolo26_fur_color_cls` is an image-level fur-color
  classification project imported from
  `D:\gogelila\Eating_behavior\data\v9_yolo26_fur_color_cls`.

## Important Paths

- Workspace: `D:\gogelila\label_studio`
- Label Studio conda env: `D:\Anaconda3\envs\label-studio`
- YOLO conda env: `D:\Anaconda3\envs\yolo`
- Label Studio database: `D:\gogelila\label_studio\label_studio_data\label_studio.sqlite3`
- Label Studio media/upload root: `D:\gogelila\label_studio\label_studio_data\media`
- SQLite CLI: `D:\Anaconda3\Library\bin\sqlite3.exe`
- Label Studio service script: `D:\gogelila\label_studio\scripts\label_studio_windows_service.py`
- Manual start script: `D:\gogelila\label_studio\start-label-studio.ps1`
- Service installer: `D:\gogelila\label_studio\install-label-studio-service.ps1`
- Fur color classification project creation script:
  `D:\gogelila\label_studio\scripts\create_fur_color_classification_project.py`
- Fur color Chinese labels/hotkeys update script:
  `D:\gogelila\label_studio\scripts\update_fur_color_zh_hotkeys.py`
- YOLO backend source/build directory: `D:\gogelila\label_studio\yolo_ls_backend`
- Docker model mount source: `D:\gogelila\Eating_behavior\yolo\best_models`

## Label Studio Service

Service name: `LabelStudio`

The service runs Label Studio with:

- `--data-dir D:\gogelila\label_studio\label_studio_data`
- `--internal-host 127.0.0.1`
- `--host http://127.0.0.1:8090`
- `--port 8090`
- `--no-browser`

Important environment variables set by both the service script and manual start script:

- `BASE_DATA_DIR=D:\gogelila\label_studio\label_studio_data`
- `LABEL_STUDIO_BASE_DATA_DIR=D:\gogelila\label_studio\label_studio_data`
- `LOCAL_FILES_SERVING_ENABLED=true`
- `LOCAL_FILES_DOCUMENT_ROOT=D:\gogelila\Eating_behavior\outputs\training_frames_4000_20260602_05_06`
- `DATA_UPLOAD_MAX_NUMBER_FILES=10000`
- `DATA_UPLOAD_MAX_MEMORY_SIZE=20000000000`
- `LATEST_VERSION_CHECK=false`

Useful commands:

```powershell
Get-Service -Name LabelStudio
Restart-Service -Name LabelStudio -Force
Invoke-WebRequest -Uri 'http://127.0.0.1:8090' -UseBasicParsing
```

## Projects

### Project 4

- ID: `4`
- Title: `eat_feed_20260602`
- Task count: `4000`
- Current prediction count: `4000`
- Current prediction model version: `cat_feeding_head_v6_yolo26s_tune2_200e_b32_wandb`
- ML backend ID: `3`
- ML backend URL: `http://127.0.0.1:9090`

### Project 5

- ID: `5`
- Title: `v1_cat`
- Task count: `4091`
- Current prediction count: `4091`
- Current prediction model version: `cat_feeding_head_v7_yolo26s_tune2_200e_b32_wandb`
- ML backend ID: `4`
- ML backend URL: `http://127.0.0.1:9090`

### Project 7

- ID: `7`
- Title: `v9_yolo26_fur_color_cls`
- Source directory: `D:\gogelila\Eating_behavior\data\v9_yolo26_fur_color_cls`
- Task count: `6831`
- Current annotation count: `3`
- Current prediction count: `6831`
- Current prediction model version: `folder_prelabel_v9_yolo26_fur_color_cls`
- Purpose: image-level fur-color classification/manual review
- Stored choice values: `white_cream_point`, `gray_brown_tabby`, `orange_cream`,
  `black_dark`, `black_white`, `calico_tortie`, `mixed_other`, `hairless_cat`
- Display labels and hotkeys:
  `1 白/奶油/重点色`, `2 灰/棕虎斑`, `3 橘/金/奶油`,
  `4 黑/深色`, `5 黑白`, `6 三花/玳瑁`, `7 混合/不确定`,
  `8 无毛猫`
- Import mode: source images were hardlinked into
  `D:\gogelila\label_studio\label_studio_data\media\upload\7`
- Project `6` / `cat_behavior_classification` was deleted during this import.
- Database backup before creation:
  `D:\gogelila\label_studio\label_studio_data\label_studio.before_fur_color_classification_20260615_104635.sqlite3`

## Current Labeling Interface

Both project `4` and project `5` currently use:

```xml
<View>
  <Image name="image" value="$image"/>
  <RectangleLabels name="label" toName="image">
    <Label value="cat" background="#1F77B4"/>
    <Label value="feeding_head" background="#D62728"/>
  </RectangleLabels>
</View>
```

Historical note: earlier work involved `cat_head` and `feeding_head`, but the
current active project labels and current YOLO model labels are `cat` and
`feeding_head`.

Project `7` uses image-level fur-color classification:

```xml
<View>
  <Image name="image" value="$image"/>
  <Choices name="fur_color" toName="image" choice="single" showInLine="true" required="true">
    <Choice value="white_cream_point" html="1 白/奶油/重点色" hint="white_cream_point" hotkey="1"/>
    <Choice value="gray_brown_tabby" html="2 灰/棕虎斑" hint="gray_brown_tabby" hotkey="2"/>
    <Choice value="orange_cream" html="3 橘/金/奶油" hint="orange_cream" hotkey="3"/>
    <Choice value="black_dark" html="4 黑/深色" hint="black_dark" hotkey="4"/>
    <Choice value="black_white" html="5 黑白" hint="black_white" hotkey="5"/>
    <Choice value="calico_tortie" html="6 三花/玳瑁" hint="calico_tortie" hotkey="6"/>
    <Choice value="mixed_other" html="7 混合/不确定" hint="mixed_other" hotkey="7"/>
    <Choice value="hairless_cat" html="8 无毛猫" hint="hairless_cat" hotkey="8"/>
  </Choices>
</View>
```

Project `7` keeps the source folder class in task data/meta and imports it as a
prediction/prelabel, not as a completed human annotation.
The `html` attribute is used for Chinese display; the stored `value` remains the
English class name so existing folder prelabels and downstream exports stay
stable.

## YOLO Preannotation Backend

Docker container:

- Name: `label-studio-yolo-preannot`
- Image: `label-studio-yolo-preannot:latest`
- Port: `9090`
- Current container ID at last check: `b4c170cf5b0c`

Current Docker env:

- `MODEL_PATH=/models/cat_feeding_head_v7_yolo26s_tune2_200e_b32_wandb.pt`
- `MODEL_VERSION=cat_feeding_head_v7_yolo26s_tune2_200e_b32_wandb`
- `CONFIDENCE_THRESHOLD=0.25`

Current Docker mounts:

- `D:\gogelila\Eating_behavior\yolo\best_models` -> `/models:ro`
- `D:\gogelila\Eating_behavior\outputs\training_frames_4000_20260602_05_06` -> `/local_files:ro`
- `D:\gogelila\label_studio\label_studio_data` -> `/label_studio_data:ro`

Current source model requested by user for the v7 `v1_cat` refresh:

```text
D:\gogelila\Eating_behavior\yolo\runs\detect\cat-head-feeding-yolo\cat_feeding_head_v7_yolo26s_tune2_200e_b32_wandb\weights\best.pt
```

Because the Docker container only mounts `best_models`, the model was copied to:

```text
D:\gogelila\Eating_behavior\yolo\best_models\cat_feeding_head_v7_yolo26s_tune2_200e_b32_wandb.pt
```

Health check:

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:9090/health' | ConvertTo-Json -Depth 5
```

Docker status:

```powershell
docker ps --filter name=label-studio-yolo-preannot --format "table {{.ID}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}\t{{.Names}}"
```

## V6 Preannotation Reset

Script:

```text
D:\gogelila\label_studio\scripts\reset_v6_preannotations.py
```

Log:

```text
D:\gogelila\label_studio\scripts\reset_v6_preannotations.log
```

The reset script does the following:

- Verifies the 9090 ML backend health and model version.
- Finds Label Studio ML backends with URL `http://127.0.0.1:9090`.
- Syncs `project.model_version`, `project.show_collab_predictions`, and `ml_mlbackend.model_version`.
- Deletes old predictions for projects using the 9090 backend.
- Keeps human annotations; for old predictions referenced by annotations, it sets `task_completion.parent_prediction_id = NULL` before deleting old prediction rows.
- Calls `/predict` in batches.
- Recreates `Prediction` rows with model version `cat_feeding_head_v6_yolo26s_tune2_200e_b32_wandb`.
- Recomputes task counters.

Last completed reset result:

- Project `4`: `4000 / 4000` predictions regenerated.
- Project `5`: `4091 / 4091` predictions currently present.

Run command:

```powershell
$env:PREDICT_BATCH_SIZE='8'
$env:PREDICT_HTTP_TIMEOUT='120'
& 'D:\Anaconda3\envs\label-studio\python.exe' 'D:\gogelila\label_studio\scripts\reset_v6_preannotations.py'
```

Verification SQL:

```powershell
& 'D:\Anaconda3\Library\bin\sqlite3.exe' -header -column 'D:\gogelila\label_studio\label_studio_data\label_studio.sqlite3' "
select id,title,model_version,show_collab_predictions from project order by id;
select id,project_id,title,model_version,state,url from ml_mlbackend order by id;
select project_id,model_version,count(*) as n from prediction group by project_id,model_version order by project_id,n desc;
select project_id,count(*) tasks,sum(total_predictions) task_prediction_counter from task group by project_id order by project_id;
"
```

## V7 `v1_cat` Preannotation Reset

Script:

```text
D:\gogelila\label_studio\scripts\reset_v7_v1_cat_preannotations.py
```

Log:

```text
D:\gogelila\label_studio\scripts\reset_v7_v1_cat_preannotations.log
```

Database backup before deleting Project `5` predictions:

```text
D:\gogelila\label_studio\label_studio_data\label_studio.before_v7_v1_cat_preannotations_20260611_093608.sqlite3
```

The v7 reset script wraps the already-tested v6 reset helpers, but restricts the
target to:

- Project ID: `5`
- Expected title: `v1_cat`
- ML backend URL: `http://127.0.0.1:9090`
- Model version: `cat_feeding_head_v7_yolo26s_tune2_200e_b32_wandb`

Last completed v7 reset result:

- Project `5`: `4091 / 4091` v7 predictions currently present.
- Project `5` task prediction counters: `sum(total_predictions)=4091`,
  `min(total_predictions)=1`, `max(total_predictions)=1`.
- Project `4` was not reset; it still has `4000` v6 predictions.

Run command:

```powershell
$env:PREDICT_BATCH_SIZE='8'
$env:PREDICT_HTTP_TIMEOUT='120'
& 'D:\Anaconda3\envs\label-studio\python.exe' 'D:\gogelila\label_studio\scripts\reset_v7_v1_cat_preannotations.py'
```

Important caveat:

- The shared Docker backend at `http://127.0.0.1:9090` now serves v7.
- Project `4` still has v6 prediction rows and its ML backend row still records
  v6, but that row points to the same `9090` service. If active project `4`
  backend inference must stay v6, recreate a separate v6 backend or move one of
  the projects to another port.

## Image URL Loading Fix

User saw:

```text
There was an issue loading URL from $image value
URL: /data/upload/5/667036ec-%E7%8C%AB1_1780883350629_8279351109.jpg
Actual failing link included %25E7%258C%25AB...
```

Root cause:

- Project `5` had many task image paths stored with percent-encoded Chinese filenames, for example `%E7%8C%AB`.
- The frontend/request path could encode `%` again, producing `%25E7%258C%25AB`.
- Label Studio's `UploadedFileResponse` looked up `FileUpload.file` using the raw URL path and did not handle the double-encoded form.
- One task also had the wrong data key `$undefined$` instead of `image`.

Data fix applied to Project `5`:

- Scanned `4093` tasks.
- Changed `2165` tasks.
- Decoded `2165` image path fields from percent encoding to normal Unicode filenames.
- Fixed `1` `$undefined$` key to `image`.

Current verification:

- Project `5` missing `image` keys: `0`
- Project `5` percent-encoded image paths: `0`

Code fix applied:

```text
D:\Anaconda3\envs\label-studio\Lib\site-packages\label_studio\data_import\api.py
```

Inside `UploadedFileResponse.get()`:

- Changed `filename = kwargs['filename']` to `filename = unquote(kwargs['filename'])`.
- Added a guard so missing `FileUpload` returns `404` instead of raising a `500`.

Relevant current lines:

```python
filename = unquote(kwargs['filename'])
file_upload = FileUpload.objects.filter(file=file).last()

if file_upload is None:
    return Response(status=status.HTTP_404_NOT_FOUND)
```

Validated after service restart:

- Unicode path returned `206 image/jpeg`.
- Single-encoded path returned `206 image/jpeg`.
- Double-encoded path returned `206 image/jpeg`.
- The formerly `$undefined$` image also returned `206 image/jpeg`.

Backup before this fix:

```text
D:\gogelila\label_studio\label_studio_data\label_studio.before_fix_image_url_encoding_20260610_132008.sqlite3
```

## Import Fixes And Limits

Earlier import failures included:

```text
The number of files exceeded settings.DATA_UPLOAD_MAX_NUMBER_FILES
You cannot access body after reading from request's data stream
```

Current upload-related settings in both manual start and service env:

- `DATA_UPLOAD_MAX_NUMBER_FILES=10000`
- `DATA_UPLOAD_MAX_MEMORY_SIZE=20000000000`

Known modified / backed-up files related to import behavior:

- `D:\gogelila\label_studio\start-label-studio.ps1`
- `D:\gogelila\label_studio\scripts\label_studio_windows_service.py`
- `D:\Anaconda3\envs\label-studio\Lib\site-packages\label_studio\core\middleware.py`
- `D:\Anaconda3\envs\label-studio\Lib\site-packages\label_studio\data_import\api.py`

Backups observed:

- `D:\gogelila\label_studio\start-label-studio.ps1.before_upload_limit_fix_20260609_100454.bak`
- `D:\gogelila\label_studio\scripts\label_studio_windows_service.py.before_upload_limit_fix_20260609_100454.bak`
- `D:\Anaconda3\envs\label-studio\Lib\site-packages\label_studio\core\middleware.py.before_import_fix_20260609_094649.bak`
- `D:\Anaconda3\envs\label-studio\Lib\site-packages\label_studio\data_import\api.py.before_multipart_import_fix_20260609_094649.bak`

## Keyboard Navigation Context

The annotation UI was customized so navigation can be done with keyboard keys:

- `A` / `a` / Chinese input mode equivalent keypress: previous task.
- `D` / `d` / Chinese input mode equivalent keypress: next task.
- Directional navigation is intended to switch only, without saving.
- Space was requested for the "save and switch" behavior.

Important behavioral constraint from debugging:

- `D` must not save, submit, or update annotations.
- `D` should only switch to the next task.
- After switching to an unannotated task, the UI should return from `Update` state to `Submit` state.

If this behavior regresses, inspect the frontend customization around global keydown handling and task switching. The goal is to trigger Label Studio's normal task navigation flow, not only replace the displayed image.

## Historical Data Operations

Project `4`, `eat_feed_20260602`:

- Earlier `cat_head` label content was removed after the label was removed from the project.
- Empty/unannotated images were set back to unconfirmed state.
- Later project labels were aligned to `cat` and `feeding_head`.

Project `5`, `v1_cat`:

- Imported images from:

```text
D:\gogelila\data\cat3_cat5_cat6_merged_yolo_verified\all_images
D:\gogelila\data\cat1_cat2_cat7_merged_yolo_v5_verified\all_images
```

- Current task count is `4091`.
- Current task image data is normalized to `$image`.

## Database Backups

Important recent backups:

```text
D:\gogelila\label_studio\label_studio_data\label_studio.before_v7_v1_cat_preannotations_20260611_093608.sqlite3
D:\gogelila\label_studio\label_studio_data\label_studio.before_fur_color_zh_hotkeys_20260615_112428.sqlite3
D:\gogelila\label_studio\label_studio_data\label_studio.before_fur_color_zh_hotkeys_20260615_105453.sqlite3
D:\gogelila\label_studio\label_studio_data\label_studio.before_fur_color_classification_20260615_104635.sqlite3
D:\gogelila\label_studio\label_studio_data\label_studio.before_v6_preannotations_20260610_092208.sqlite3
D:\gogelila\label_studio\label_studio_data\label_studio.before_fix_image_url_encoding_20260610_132008.sqlite3
```

Older `.bak` files exist in:

```text
D:\gogelila\label_studio\label_studio_data
```

Examples include backups before deleting `cat_head`, before refreshing predictions, and before importing `v1_cat`.

## Common Verification Commands

Label Studio service:

```powershell
Get-Service -Name LabelStudio | Select-Object Name,Status,StartType
Invoke-WebRequest -Uri 'http://127.0.0.1:8090' -UseBasicParsing
```

YOLO backend:

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:9090/health' | ConvertTo-Json -Depth 5
docker ps --filter name=label-studio-yolo-preannot --format "table {{.ID}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}\t{{.Names}}"
```

Projects, ML backends, and predictions:

```powershell
& 'D:\Anaconda3\Library\bin\sqlite3.exe' -header -column 'D:\gogelila\label_studio\label_studio_data\label_studio.sqlite3' "
select id,title,model_version,show_collab_predictions from project order by id;
select id,project_id,title,model_version,state,url from ml_mlbackend order by id;
select project_id,model_version,count(*) as n from prediction group by project_id,model_version order by project_id,n desc;
select project_id,count(*) tasks,sum(total_predictions) task_prediction_counter from task group by project_id order by project_id;
"
```

Project `5` image data sanity:

```powershell
& 'D:\Anaconda3\Library\bin\sqlite3.exe' -header -column 'D:\gogelila\label_studio\label_studio_data\label_studio.sqlite3' "
select count(*) as project5_missing_image from task where project_id=5 and json_extract(data,'$.image') is null;
select count(*) as project5_percent_image_tasks from task where project_id=5 and json_extract(data,'$.image') like '%\%%' escape '\';
"
```

## Notes For Future Model Replacement

If a future model path is outside `D:\gogelila\Eating_behavior\yolo\best_models`, copy it into that directory first, because the Docker backend mounts only that directory as `/models:ro`.

Recommended workflow:

1. Copy the `.pt` file into `D:\gogelila\Eating_behavior\yolo\best_models`.
2. Recreate or restart the `label-studio-yolo-preannot` Docker container with updated:
   - `MODEL_PATH=/models/<model-file>.pt`
   - `MODEL_VERSION=<model-version-name>`
3. Check `http://127.0.0.1:9090/health`.
4. Update Label Studio project and ML backend model version.
5. Delete old predictions only after backing up the SQLite database.
6. Regenerate preannotations with a script similar to `scripts\reset_v6_preannotations.py`.
7. Verify prediction counts and `task.total_predictions`.

## Known Caveats

- This workspace is not currently a Git repository, so changes are tracked through file backups and this context document.
- Some fixes are applied directly inside the conda env's installed Label Studio package under `D:\Anaconda3\envs\label-studio\Lib\site-packages\label_studio`. Reinstalling or upgrading Label Studio may overwrite these fixes.
- The `/data/upload` URL fix should be preserved if Label Studio is reinstalled.
- The service must be restarted after changes under `site-packages`.
