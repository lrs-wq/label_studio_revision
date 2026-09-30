# Label Studio 标注工作台

这个仓库保存服务器上的 Label Studio 定制代码、Windows / Linux 部署脚本、YOLO 预标注后端，以及围绕猫和狗数据集的导入、清洗、预标注和导出脚本。

本仓库只上传代码、配置和说明文档。图片、视频、标注数据、数据库、模型权重、密钥、日志、缓存和备份不上传；相关目录及文件类型由 `.gitignore` 排除。

## 服务器代码快照

- 整理日期：2026-09-30（Asia/Shanghai）
- 来源目录：`39.106.120.253:/root/label_studio`
- 代码取自服务器当前文件，包含服务器上尚未提交到旧仓库的修改。
- `LABEL_STUDIO_CONTEXT.md` 是 2026-06-15 的 Windows 环境历史记录；Linux 部署以 `deploy/` 内的脚本和服务配置为准。
- 仓库保存 Label Studio 的部署和定制补丁；上游 Label Studio 包通过部署脚本安装，不包含 Python 虚拟环境或第三方安装包。

## 目录结构

```text
.
├── deploy/                      # Linux 部署脚本、systemd 服务和 Nginx 示例
├── patches/label_studio/        # 前端快捷键、可见性及视频上传转码补丁
├── scripts/                     # Label Studio 项目维护、导入、导出和预标注脚本
├── dog_pose_ls_backend/         # 狗姿态关键点预标注后端
├── yolo_ls_backend/             # YOLO 检测预标注后端，输出矩形框
├── yolo_pose_ls_backend/        # YOLO pose 预标注后端，输出关键点
├── install-label-studio-service.ps1
├── start-label-studio.ps1       # 手动启动 Label Studio
├── stop-label-studio.ps1        # 停止 Windows 服务和 8090 端口进程
├── README.md                    # 安装、部署和源码说明
├── LABEL_STUDIO_CONTEXT.md      # 历史运行状态和排障记录
└── .gitignore
```

## 运行环境

Windows 脚本沿用以下默认路径：

- 项目目录：`D:\gogelila\label_studio`
- Label Studio Conda 环境：`D:\Anaconda3\envs\label-studio`
- Label Studio 数据目录：`D:\gogelila\label_studio\label_studio_data`
- 本地文件访问根目录：`D:\gogelila\Eating_behavior\outputs\training_frames_4000_20260602_05_06`
- 服务端口：`8090`
- 公开访问地址：`http://192.168.1.200:8090`
- 公开注册：已关闭，`DISABLE_SIGNUP_WITHOUT_LINK=true`

如果迁移到其他机器，需要同步修改 `start-label-studio.ps1` 和 `scripts/label_studio_windows_service.py` 中的路径、IP 和端口配置。

## 启动 Label Studio

以管理员身份打开 PowerShell，在项目目录执行：

```powershell
.\install-label-studio-service.ps1
```

该脚本会安装或重装 Windows 服务 `LabelStudio`，设置为自动启动，并立即启动服务。

常用服务命令：

```powershell
Get-Service -Name LabelStudio
Restart-Service -Name LabelStudio -Force
Stop-Service -Name LabelStudio -Force
```

手动启动可执行：

```powershell
.\start-label-studio.ps1
```

停止服务和残留进程可执行：

```powershell
.\stop-label-studio.ps1
```

启动后访问：

- 局域网：`http://192.168.1.200:8090`
- 本机：`http://127.0.0.1:8090`

Windows 环境历史配置禁止无邀请链接注册；现有账号及标注项目保存在数据库中，不包含在源码仓库内。
当公开注册关闭时，登录页的注册入口会保留为提示按钮；点击后会提示“注册功能已关闭，请使用现有账号登录。”。该 UI 补丁由 `scripts/apply_label_studio_signup_disabled_notice.py` 应用，Windows 和 Linux 启动脚本会在启动 Label Studio 前自动执行。

Label Studio 运行时还有一组仓库化补丁，由 `scripts/apply_label_studio_runtime_patches.py` 自动应用：

- 复制 `patches/label_studio/custom-pose-visibility.js` 到前端运行时目录，恢复猫姿态可见性结果补齐逻辑。
- patch 已安装包中的 `label_studio/templates/base.html`，加载 `custom-pose-visibility.js`，并注入 A/D/Space 快捷键：
  - `A`：上一张，不保存
  - `D`：下一张，不保存
  - `Space`：先提交或更新当前标注，再进入下一张
- 标注页底部增加“清空标注”按钮。按钮会二次确认并删除当前任务已保存的人工标注；未提交草稿会被丢弃，预标注不受影响。

服务器的 `deploy/label-studio.service` 和 `deploy/start-label-studio.sh` 默认将 `PUBLIC_HOST` 配置为 `https://ai.hzzootech.com`，后端监听 `127.0.0.1:8090`。迁移时需同步设置外部访问地址及 `CSRF_TRUSTED_ORIGINS`，避免反代地址与应用地址不一致导致登录失败。

## Linux 服务器部署

默认项目目录为 `/root/label_studio`，持久素材和模型位于 `/data/label_studio`。

```bash
git clone https://github.com/lrs-wq/label_studio_revision.git /root/label_studio
cd /root/label_studio
bash deploy/remote-setup.sh
systemctl start label-studio.service
systemctl status label-studio.service
```

`remote-setup.sh` 安装 `label-studio==1.23.0`，建立数据目录链接并安装、启用服务。私有仓库克隆需要具备仓库读取权限。

猫检测与猫姿态后端：

```bash
bash deploy/setup-yolo-backends.sh
systemctl start label-studio-yolo.service label-studio-yolo-pose.service
```

两个后端默认监听 `127.0.0.1:9090` 和 `127.0.0.1:9091`，模型路径由各自的 service 文件配置。

狗姿态 Docker 后端：

```bash
MODEL_FILE=/data/label_studio/models/dog_pose_yolo26m_best.pt \
  bash deploy/run-dog-pose-docker.sh
curl --fail http://127.0.0.1:9094/health
```

狗姿态后端需要单独准备模型文件，脚本默认映射到本机 `9094` 端口。

`deploy/nginx/label-studio-proxy.conf` 是 HTTP 反代示例，仍使用服务器 IP。使用域名和 HTTPS 时，应结合实际 Nginx 配置设置域名、证书和转发协议；证书及私钥不提交到本仓库。

启动脚本自动应用运行时补丁，包括 `A` / `D` / `Space` 导航与提交、视频逐帧快捷键、清空标注、检测框显示切换，以及 MOV / MKV 上传转 MP4。视频转码需要服务器提供 `ffmpeg` 和 `ffprobe`。

新增的数据集工具包括 `scripts/create_dog_pose_dataset_project.py`、`scripts/create_dog_pose_preannotation_project.py`、`scripts/generate_dog_pose_preannotations.py`、`scripts/convert_dog_pose_project_to_reference_17.py` 和 `scripts/import_ogaith_label_studio.py`。运行前应检查各脚本中的路径、项目 ID 和参数。

## YOLO 预标注后端

`yolo_ls_backend/` 提供矩形框检测预标注服务，兼容 Label Studio ML backend 的 `/setup` 和 `/predict` 接口。

主要环境变量：

- `MODEL_PATH`：容器内模型路径，默认 `/models/best.pt`
- `MODEL_VERSION`：写入 Label Studio prediction 的模型版本
- `LABEL_STUDIO_DATA_DIR`：容器内 Label Studio 数据目录，默认 `/label_studio_data`
- `LOCAL_FILES_DOCUMENT_ROOT`：容器内本地文件根目录，默认 `/local_files`
- `CONFIDENCE_THRESHOLD`：检测阈值，默认 `0.25`
- `MAX_DETECTIONS`：最大检测数，默认 `300`

健康检查：

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:9090/health' | ConvertTo-Json -Depth 5
```

`yolo_pose_ls_backend/` 提供关键点预标注服务，默认输出猫姿态关键点和可见性选择结果。它同样监听 `9090`，如需同时运行检测和姿态服务，需要映射到不同宿主机端口。

## 常用脚本

项目创建和配置：

- `scripts/create_cat_pose_keypoint_project.py`
- `scripts/create_cat_pose_seed_800_project.py`
- `scripts/create_fur_color_classification_project.py`
- `scripts/update_cat_pose_visibility_config.py`
- `scripts/update_fur_color_zh_hotkeys.py`

数据清洗和导入：

- `scripts/clean_cat_eating_drinking_manifest.py`
- `scripts/import_cat_feed_260602.py`
- `scripts/remove_fur_color_small_area_images.py`

预标注重置：

- `scripts/reset_v6_preannotations.py`
- `scripts/reset_v7_v1_cat_preannotations.py`
- `scripts/reset_pose_project9_preannotations.py`

导出：

- `scripts/export_cat_pose_yolo_pose.py`
- `scripts/export_fur_color_cls_v2.py`

视频和图片处理：

- `scripts/extract_video_frames.py`
- `scripts/extract_frames_every_100.py`
- `scripts/download_cat_context_videos.py`
- `scripts/download_cat_eating_drinking_bulk.py`
- `scripts/download_cat_eating_drinking_youtube_fill.py`
- `scripts/download_cat_images_coco.py`

运行脚本前建议先确认 `label_studio_data/label_studio.sqlite3` 已备份，尤其是会批量删除、重置 prediction 或 annotation 的脚本。

## 数据和安全说明

以下内容只保留在服务器或独立数据存储中，不提交到仓库：

- `label_studio_data/`：Label Studio 数据库、上传文件、`secret_key.txt`
- `logs/`：服务日志
- `tmp/`：临时压缩包和处理产物
- `pip_cache/`：Python 包缓存
- `cat_video_assets/`：视频素材和大文件
- `/data/label_studio/`：外部媒体、素材、模型和本地文件
- 图片、视频、音频、标注导出文件和模型权重
- `.env`、密钥、证书、数据库、日志、备份文件和 Python 编译产物

如需共享数据集或模型，请使用独立的数据/模型存储，不要直接放入当前 Git 仓库。

## Git

远程仓库：

```text
https://github.com/lrs-wq/label_studio_revision.git
```

常用提交流程：

```powershell
git status
git diff --stat
git add <代码或文档路径>
git commit -m "Describe changes"
git push
```

更多历史运行状态、项目 ID、模型版本和排障记录见 `LABEL_STUDIO_CONTEXT.md`。

本次导入只包含服务器当前的代码和文档快照，不导入旧仓库的 Git 历史，避免历史提交夹带数据文件。后续提交前应检查暂存清单，确认只包含代码、配置及文档。
