# Annotation Pipeline Skill 技术架构文档

## 1. 文档目标

本文档定义 `annotation-pipeline-skill` 的目标技术架构。重点不是复刻 `memory-ner` 的脚本实现，而是把其中通用、可复用的工程机制抽象为开源 skill 的内核。


## 2. 设计目标

### 2.1 主要目标

- 支持任意标注任务类型，不写死 NER
- 支持多阶段流水线，而不是单步脚本
- 保证 task 级 traceability
- 支持 deterministic gate、QC、repair、merge
- 支持本地最小运行模式和可替换的重型运行模式
- 让业务特定逻辑以插件形式接入

### 2.2 非目标

- 不把所有运行模式都塞进核心
- 不要求默认依赖 Redis、Docker、systemd
- 不让 provider、schema、dataset 逻辑污染框架核心


## 3. 总体架构

建议采用分层架构：

1. `Core Domain`
2. `Application Services`
3. `Plugin Contracts`
4. `Runtime Backends`
5. `Interfaces`
6. `Integration Adapters`

### 3.1 分层说明

#### Core Domain

负责：

- task 状态机
- attempt 模型
- artifact 元数据
- transition rules
- audit event 结构

不负责：

- 如何调用模型
- 如何读取特定数据源
- 如何落地到 Redis / systemd / Web UI

#### Application Services

负责：

- 创建 task
- 推进阶段
- 执行 retry policy
- 组织 validate / qc / merge 调用
- 协调 store、runtime、plugins

#### Plugin Contracts

负责定义接口：

- dataset adapter
- validator
- prompt builder
- qc policy
- repair strategy
- merge sink
- provider client

#### Runtime Backends

可选实现：

- local subprocess
- queue + worker
- systemd-based runtime

#### Interfaces

用户接入层：

- CLI
- dashboard API
- TypeScript Web UI

#### Integration Adapters

负责外部系统边界：

- 外部任务 API 拉取
- 外部 task id 与内部 task id 映射
- 阶段状态回传
- 结果提交
- 幂等、重试和 dead-letter 记录

Integration adapter 不能直接改写 task JSON，必须通过 application service 创建 task 和推进状态。


## 4. 核心模块划分

建议目录：

```text
annotation_pipeline_skill/
  core/
    models.py
    states.py
    transitions.py
    events.py
  services/
    task_factory.py
    pipeline_service.py
    retry_service.py
    merge_service.py
    dashboard_service.py
    settings_service.py
    external_task_service.py
    feedback_service.py
  store/
    base.py
    file_store.py
  runtime/
    base.py
    local_subprocess.py
    queued_runtime.py
  plugins/
    base.py
    registry.py
  interfaces/
    cli.py
    api.py
  web/
    package.json
    tsconfig.json
    src/
      api/
      components/
      pages/
      types/
  templates/
    project/
    adapters/
  examples/
    jsonl_demo/
```


## 5. 核心领域模型

### 5.1 ProjectConfig

```python
ProjectConfig:
  project_id: str
  root_dir: str
  task_store_backend: str
  runtime_backend: str
  concurrency: dict
  plugins: dict
  providers: dict
  annotators: dict
  stage_routes: dict
  external_task_api: dict | None
```

### 5.2 Task

```python
Task:
  task_id: str
  pipeline_id: str
  source_ref: SourceRef
  external_ref: ExternalTaskRef | None
  modality: str
  annotation_requirements: dict
  selected_annotator_id: str | None
  status: TaskStatus
  current_attempt: int
  assignee: str | None
  created_at: datetime
  updated_at: datetime
  active_run_id: str | None
  next_retry_at: datetime | None
  metadata: dict
```

### 5.2.1 ExternalTaskRef

```python
ExternalTaskRef:
  system_id: str
  external_task_id: str
  source_url: str | None
  idempotency_key: str
  last_status_posted: str | None
  last_status_posted_at: datetime | None
  submit_attempts: int
```

`ExternalTaskRef` 是 integration 边界元数据。core 可以保存引用，但不能包含外部 API client 逻辑。

### 5.3 Attempt

```python
Attempt:
  attempt_id: str
  task_id: str
  index: int
  stage: StageName
  status: AttemptStatus
  started_at: datetime | None
  finished_at: datetime | None
  provider_id: str | None
  model: str | None
  effort: str | None
  route_role: str | None  # primary | fallback | override
  summary: str | None
  error: ErrorInfo | None
  artifacts: list[ArtifactRef]
```

### 5.3.1 AnnotatorProfile

```python
AnnotatorProfile:
  annotator_id: str
  display_name: str
  modality: list[str]  # text | image | video | point_cloud
  annotation_types: list[str]
  input_artifact_kinds: list[str]
  output_artifact_kinds: list[str]
  provider_route_id: str | None
  external_tool_id: str | None
  preview_renderer_id: str | None
  human_review_policy_id: str | None
  fallback_annotator_id: str | None
  enabled: bool
  metadata: dict
```

`AnnotatorProfile` 描述“谁能标什么”。它和 `ProviderConfig` 分离：provider 解决调用模型或服务的问题，annotator profile 解决 task 能力匹配、输出 artifact，以及是否为 QC/Human Review 生成 preview 证据的问题。

### 5.4 ArtifactRef

```python
ArtifactRef:
  artifact_id: str
  task_id: str
  kind: str
  path: str
  content_type: str
  created_at: datetime
  metadata: dict
```

多模态 artifact 应通过 `kind` 和 `metadata` 描述输入输出。例如：

- `image_source`：原图路径、尺寸、颜色空间
- `image_bbox_annotation`：box 坐标、label、confidence、source model
- `image_bbox_preview`：渲染后的 overlay 图片
- `video_frame_annotation`：frame index、timestamp、box/mask/track
- `point_cloud_annotation`：3D box、coordinate frame、instance id

### 5.5 FeedbackRecord

```python
FeedbackRecord:
  feedback_id: str
  task_id: str
  attempt_id: str | None
  source: str  # validator | qc_provider | human_review | merge_gate
  severity: str  # info | warning | error | blocker
  code: str
  message: str
  location: FeedbackLocation | None
  artifact_refs: list[ArtifactRef]
  suggested_action: str  # bulk_code_repair | annotator_rerun | manual_annotation | reject
  repair_decision: str | None
  status: str  # open | applied | dismissed | superseded
  created_at: datetime
  resolved_at: datetime | None
  metadata: dict

FeedbackLocation:
  source_line: int | None
  output_line: int | None
  span: str | None
  entity_id: str | None
  json_path: str | None
```

`FeedbackRecord` 是 annotator 和 repair strategy 的共同输入。它不能只存在于 prompt 文本里，必须能在 task detail、audit history 和 repair context 中被追踪。

### 5.6 AuditEvent

```python
AuditEvent:
  event_id: str
  task_id: str
  type: str
  actor: str
  timestamp: datetime
  payload: dict
```


## 6. 状态机设计

### 6.1 Task 状态

```text
draft
ready
annotating
validating
qc
human_review
repair_needed
accepted
rejected
merged
blocked
retry_scheduled
cancelled
```

### 6.2 状态转换原则

- 所有状态转换必须通过统一 service 完成
- worker 不直接任意改写 task 文件
- 每次转换必须写 audit event
- 运行时状态和业务状态分离

### 6.3 运行时状态

运行时状态单独建模为 `RunLease` 或 `ExecutionRecord`：

```python
ExecutionRecord:
  run_id: str
  task_id: str
  stage: str
  runtime_backend: str
  provider_id: str | None
  model: str | None
  worker_id: str | None
  pid: int | None
  lease_expires_at: datetime | None
  heartbeat_at: datetime | None
  status: str
```

理由：

- 避免 task 文件既承担业务状态又承担活跃进程注册
- 可以更清晰地做 crash recovery


## 7. 存储架构

### 7.1 默认实现：文件系统 Store

MVP 使用文件系统，建议结构：

```text
.annotation-pipeline/
  project.json
  tasks/
    <task_id>.json
  attempts/
    <task_id>/
      attempt-001.json
  events/
    <task_id>.jsonl
  runtime/
    runs/
      <run_id>.json
  settings/
    providers.json
    annotators.json
    stage_routes.json
    scheduler.json
  external/
    inbox/
    outbox/
    dead_letters/
  artifacts/
    <task_id>/
      raw_slice.jsonl
      output.jsonl
      validation.json
      qc.json
      merge.json
      previews/
        bbox_overlay.png
  feedback/
    <task_id>.jsonl
  snapshots/
    dashboard.json
```

### 7.2 存储原则

- task 是 canonical business state
- event 是 append-only audit trail
- artifacts 与 task 解耦，只通过引用挂接
- media previews 是 artifact，不是业务状态；重新渲染 preview 不应改变 task 状态
- feedback 是 append-only 修复输入，不应被覆盖；新的 QC/validation 结果只能追加或 supersede 旧反馈
- runtime records 可被重建，不应成为业务真相唯一来源
- settings 是调度和 provider routing 的 canonical config，不写入 provider secret 明文
- external outbox 是外部状态回传的可靠队列，不能只依赖同步 HTTP 调用成功

### 7.3 原子性要求

- task 写入必须原子化
- event 追加必须尽量单向、不回写
- artifact 输出完成前先写临时文件再 rename
- 对单 task 更新加细粒度锁


## 8. 插件接口设计

### 8.1 DatasetAdapter

负责：

- 读取源数据
- 产出切片
- 生成 source manifest
- 构造 merge 所需 source key

接口建议：

```python
class DatasetAdapter(Protocol):
    def discover_sources(self, config: dict) -> list[SourceRef]: ...
    def build_tasks(self, source: SourceRef, task_size: int) -> Iterable[TaskDraft]: ...
    def build_manifest(self, draft: TaskDraft) -> dict: ...
```

### 8.2 PromptBuilder

负责：

- 生成 annotation prompt
- 生成 QC prompt
- 生成 repair prompt
- 将 open feedback records 压缩成 annotator 可执行的 compact feedback bundle

接口建议：

```python
class PromptBuilder(Protocol):
    def build_annotation_prompt(self, context: AnnotationContext) -> str: ...
    def build_qc_prompt(self, context: QcContext) -> str: ...
    def build_repair_prompt(self, context: RepairContext) -> str: ...
    def build_feedback_bundle(self, records: list[FeedbackRecord]) -> str: ...
```

### 8.2.1 AnnotatorSelector

负责：

- 根据 task manifest 的 modality、annotation requirements、artifact kind 选择 annotator
- 校验 annotator profile 是否支持目标输入和输出 artifact
- 在 primary annotator 不可用时选择 fallback annotator 或人工队列
- 记录选择原因和 capability match 结果

接口建议：

```python
class AnnotatorSelector(Protocol):
    def select(self, task: Task, profiles: list[AnnotatorProfile]) -> AnnotatorSelection: ...
```

选择逻辑必须基于结构化 manifest 和 profile 能力声明，不能基于 task 文本硬编码关键词。

### 8.3 Validator

负责：

- schema 校验
- deterministic lint
- merge gate

接口建议：

```python
class Validator(Protocol):
    def validate_output(self, task: Task, artifact: ArtifactRef) -> ValidationResult: ...
```

### 8.4 QcPolicy

负责：

- 抽样策略
- 通过阈值
- verdict 计算
- 基于结构化 QC 结果输出 `human_review_required` 和 review reason
- 支持 pipeline 强制 review 与 QC risk review 的合并决策

### 8.5 RepairStrategy

负责：

- 基于 validation/QC 结果决定 patch、rerun 或 escalate
- 基于 feedback records 选择 `bulk_code_repair`、`annotator_rerun`、`manual_annotation` 或 `reject`
- 为 annotator rerun 生成 repair context
- 为 bulk repair 返回 deterministic patch plan 或 repair artifact

### 8.6 MergeSink

负责：

- 将 accepted 结果写入目标系统
- 返回 merge report

### 8.6.1 PreviewRenderer

负责多模态 annotation artifact 的可视化预览：

```python
class PreviewRenderer(Protocol):
    def render(self, task: Task, source: ArtifactRef, annotation: ArtifactRef) -> ArtifactRef: ...
```

MVP 可以先实现 `ImageBoundingBoxRenderer`：

- 输入：`image_source` + `image_bbox_annotation`
- 输出：`image_bbox_preview`
- 坐标系统、图像尺寸、label 和 confidence 必须写入 metadata
- renderer 只生成 preview artifact，不决定 task 是否进入 QC

### 8.7 ProviderClient

负责：

- 调用具体模型供应商
- 返回结构化执行结果

Provider 不应感知 task store 和业务状态机。

### 8.8 ExternalTaskAdapter

负责从外部任务系统获取任务、提交结果和回传状态：

```python
class ExternalTaskAdapter(Protocol):
    def pull_tasks(self, limit: int) -> Iterable[ExternalTaskEnvelope]: ...
    def acknowledge_task(self, external_ref: ExternalTaskRef) -> None: ...
    def post_status(self, external_ref: ExternalTaskRef, status: ExternalTaskStatus) -> None: ...
    def submit_result(self, external_ref: ExternalTaskRef, result: ExternalTaskResult) -> None: ...
```

设计要求：

- 所有请求必须使用 idempotency key。
- 外部 API 错误不能丢失内部状态转换；失败状态回传写入 outbox 并由 retry drain 处理。
- adapter 返回的是外部 envelope，内部 task 仍由 `ExternalTaskService` 调用 `TaskFactoryService` 创建。
- MVP 只支持 pull + status callback + submit result；webhook ingestion 留到后续版本。

### 8.9 ProviderRegistry 和 StageRouter

Provider 配置分两层：

```python
ProviderConfig:
  provider_id: str
  kind: str
  models: list[str]
  default_model: str
  effort_options: list[str]
  secret_ref: str | None
  enabled: bool
  metadata: dict

StageRoute:
  stage: str
  primary_provider_id: str
  primary_model: str
  primary_effort: str | None
  fallback_provider_id: str | None
  fallback_model: str | None
  fallback_effort: str | None
  fallback_delay_seconds: int
  pause_until: datetime | None
  pause_reason: str | None
```

`StageRouter` 根据 stage、settings、task binding、provider pause 状态选择 route。已经绑定会话的 task 可以要求继续使用同一 provider；这种约束必须作为 route decision reason 写入 audit event。


## 9. 应用服务设计

### 9.1 TaskFactoryService

职责：

- 调用 adapter 创建 task draft
- 写入 raw slice / manifest
- 初始化 task

### 9.2 PipelineService

职责：

- 推进 task through stages
- 与 runtime backend 协作分配执行
- 汇总插件结果并决定下一状态
- 调用 `AnnotatorSelector` 选择合适 annotator
- 对启用 Human Review 的 task，在 QC 后进入人工复核阶段

### 9.3 RetryService

职责：

- 区分运行时错误和业务错误
- 根据 policy 生成 retry schedule
- 生成 repair context

### 9.3.1 FeedbackService

职责：

- 从 validator result、QC artifact、merge gate 和 human review 中生成 `FeedbackRecord`
- 读取当前 open feedback records，并按 task、attempt、severity、code 汇总
- 将旧反馈标记为 applied、dismissed 或 superseded
- 为 annotator rerun 构建 compact feedback bundle
- 为看板提供 feedback history read model
- 记录 operator 对 repair decision 的 override audit event

### 9.4 DashboardService

职责：

- 汇总 task 状态
- 提供 operator 所需最小视图
- 构建 dashboard read model
- 刷新 runtime overlay，避免缓存 snapshot 掩盖真实 worker 状态
- 输出 task detail payload，包括 attempts、events、artifacts、feedback records、provider route、external ref
- 输出 annotator profile、capability match、media preview artifact 和 Human Review 状态

### 9.5 SettingsService

职责：

- 读取 scheduler 设置：并发、每周期启动上限、自动派发开关
- 读取 provider registry、stage routes 和 annotator profiles
- 校验 provider/model/effort 是否属于可用选项
- 校验 annotator capability 是否满足 task requirements
- 提供 provider connectivity test 和 route validation
- MVP 不通过 UI/API 写入 provider、stage route 或 annotator YAML

### 9.6 ExternalTaskService

职责：

- 调用 `ExternalTaskAdapter.pull_tasks`
- 将外部 envelope 映射为 `TaskDraft`
- 创建带 `ExternalTaskRef` 的内部 task
- 在 stage transition 后写入 external status outbox
- 在 accepted / rejected / merged 后提交结果或失败原因
- 处理外部 API 幂等冲突、临时失败和 dead-letter


## 10. Runtime 设计

### 10.1 RuntimeBackend 抽象

```python
class RuntimeBackend(Protocol):
    def submit(self, run_spec: RunSpec) -> ExecutionRecord: ...
    def poll(self, run_id: str) -> ExecutionRecord: ...
    def cancel(self, run_id: str) -> None: ...
    def heartbeat(self) -> RuntimeHealth: ...
```

### 10.2 MVP：LocalSubprocessRuntime

默认用本地 subprocess 实现，特点：

- 无额外基础设施
- 易于开源用户上手
- 适合单机小规模运行

### 10.3 可选：QueuedRuntime

用于更大规模或长期运行场景：

- queue backend
- worker pool
- lease + heartbeat
- delayed retry

### 10.4 SystemdRuntime 作为扩展而非默认

如果未来保留 systemd 模式，应放在可选插件里，而不是核心默认能力。


## 11. 执行模型

### 11.1 Annotate 阶段

1. scheduler 选择 `ready` task
2. `AnnotatorSelector` 根据 modality 和 annotation requirements 选择 annotator
3. 创建 `ExecutionRecord`
4. runtime 提交 worker 或 external tool call
5. worker 执行 provider call、外部模型调用或人工队列分配
6. 写出 annotation artifact
7. 如果 annotator profile 配置了 preview renderer，`PreviewRenderer` 生成 preview artifact
8. pipeline 将 task 推进到 `validating`

### 11.1.1 多模态图片检测示例

1. task manifest 声明 `modality=image`、`annotation_type=bounding_box`
2. `AnnotatorSelector` 选择支持 image/bounding_box 的 VC detection annotator
3. annotator 调用检测模型，生成 `image_bbox_annotation`
4. `ImageBoundingBoxRenderer` 生成 `image_bbox_preview`
5. task 进入 validation 和 QC
6. 如果 Human Review policy 启用，QC 后进入 `human_review`
7. TypeScript 看板展示 overlay 图片，reviewer 决定 accept、reject 或 request repair

### 11.2 Validate 阶段

1. validator 读取输出 artifact
2. 返回 `ValidationResult`
3. 若通过，进入 `qc`
4. 若失败，进入 `repair_needed`

### 11.3 QC 阶段

1. `QcPolicy` 生成 sample
2. QC worker 或 deterministic checker 评估
3. 生成 QC artifact
4. 若通过，合并 pipeline review policy 和 QC risk decision
5. 若任一 policy 要求 review，进入 `human_review`
6. 若都不要求 review，进入 `accepted`
7. 若失败，`FeedbackService` 从 QC artifact 生成 `FeedbackRecord`
8. `RepairStrategy` 基于 feedback records 选择 repair decision
9. 根据 repair decision 进入 `repair_needed`、`validating` 或 `blocked`

### 11.3.1 Human Review 阶段

Human Review 是 QC 后的可选阶段，采用混合触发策略：

1. pipeline policy 可以强制 review
2. QC policy 可以基于风险要求 review
3. 任一 policy 命中时进入 `human_review`
4. dashboard 展示 QC artifact、feedback summary、review reason 和 media preview artifacts
5. reviewer 可以 `accept`、`reject` 或 `request_repair`
6. `accept` 推进到 `accepted`
7. `reject` 推进到 `rejected`
8. `request_repair` 生成或更新 `FeedbackRecord`，并进入 `repair_needed`
9. 所有 reviewer 动作必须写 audit event

### 11.4 Repair 阶段

1. `FeedbackService` 读取 open feedback records
2. `RepairStrategy` 决定 `bulk_code_repair`、`annotator_rerun`、`manual_annotation` 或 `reject`
3. `bulk_code_repair` 生成 deterministic repair artifact，回到 `validating`
4. `annotator_rerun` 生成 compact feedback bundle 和 repair prompt，回到 `annotating`
5. `manual_annotation` 进入人工队列或 `blocked`
6. 每次 repair decision 和 operator override 都写 audit event

### 11.5 Merge 阶段

1. `MergeSink` 接收 accepted artifact
2. 写回目标 truth store
3. 返回 merge report
4. task 标记 `merged`


## 12. 错误模型

### 12.1 错误分类

- `RuntimeError`
  - worker crash
  - timeout
  - backend unavailable
- `ProviderError`
  - rate limit
  - malformed provider response
- `ValidationError`
  - schema invalid
  - line count mismatch
  - manifest mismatch
- `QualityError`
  - QC threshold not met
- `MergeError`
  - sink write failure

### 12.2 错误处理原则

- 运行时错误可以自动 retry
- 业务质量错误进入 repair or review
- merge 错误不回滚 task trace，只记录失败 attempt
- 所有错误必须结构化记录


## 13. 配置架构

### 13.1 配置层级

```text
project.yaml
pipeline.yaml
providers.yaml
stage_routes.yaml
annotators.yaml
adapters/<adapter>.yaml
external_tasks.yaml
```

### 13.2 配置示例

```yaml
project:
  id: demo-jsonl
  runtime_backend: local_subprocess
  task_store: file_store

pipeline:
  task_size: 500
  concurrency:
    annotation: 8
    qc: 2
  retry:
    runtime_error_delay_seconds: 3600
    max_attempts: 5

plugins:
  dataset_adapter: jsonl_basic
  prompt_builder: jsonl_basic
  validator: schema_v1
  qc_policy: random_sample_50
  merge_sink: file_append

providers:
  general_llm:
    kind: openai_compatible
    models: ["general-large", "general-small"]
    default_model: general-large
    effort_options: ["low", "medium", "high"]
    secret_ref: env:GENERAL_LLM_API_KEY
    enabled: true
  review_llm:
    kind: chat_completion
    models: ["review-large", "review-fast"]
    default_model: review-large
    effort_options: ["low", "medium", "high"]
    secret_ref: env:REVIEW_LLM_API_KEY
    enabled: true

stage_routes:
  annotation:
    primary_provider_id: general_llm
    primary_model: general-large
    primary_effort: medium
    fallback_provider_id: review_llm
    fallback_model: review-fast
    fallback_effort: high
    fallback_delay_seconds: 3600
  qc:
    primary_provider_id: review_llm
    primary_model: review-large
    primary_effort: high
    fallback_provider_id: general_llm
    fallback_model: general-large
    fallback_effort: high
    fallback_delay_seconds: 3600
  repair:
    primary_provider_id: general_llm
    primary_model: general-large
    primary_effort: high
    fallback_provider_id: review_llm
    fallback_model: review-large
    fallback_effort: high
    fallback_delay_seconds: 3600
  merge:
    primary_provider_id: general_llm
    primary_model: general-small
    primary_effort: high
    fallback_provider_id: general_llm
    fallback_model: general-large
    fallback_effort: high
    fallback_delay_seconds: 3600

annotators:
  text_extraction_default:
    display_name: Default text extractor
    modality: ["text"]
    annotation_types: ["extraction", "classification"]
    input_artifact_kinds: ["raw_slice"]
    output_artifact_kinds: ["output"]
    provider_route_id: annotation
    preview_renderer_id: null
    human_review_policy_id: null
    fallback_annotator_id: null
    enabled: true
  vc_detection_bbox:
    display_name: VC detection bounding box annotator
    modality: ["image"]
    annotation_types: ["bounding_box"]
    input_artifact_kinds: ["image_source"]
    output_artifact_kinds: ["image_bbox_annotation"]
    provider_route_id: null
    external_tool_id: vc_detection
    preview_renderer_id: image_bbox_renderer
    human_review_policy_id: image_bbox_spot_check
    fallback_annotator_id: human_image_bbox_queue
    enabled: true
  human_image_bbox_queue:
    display_name: Human image bbox queue
    modality: ["image"]
    annotation_types: ["bounding_box"]
    input_artifact_kinds: ["image_source"]
    output_artifact_kinds: ["image_bbox_annotation"]
    provider_route_id: null
    external_tool_id: human_queue
    preview_renderer_id: image_bbox_renderer
    human_review_policy_id: image_bbox_required_review
    fallback_annotator_id: null
    enabled: true

external_tasks:
  enabled: false
  adapter: http_json
  pull_url: https://tasks.example.com/api/tasks/pull
  status_url: https://tasks.example.com/api/tasks/status
  submit_url: https://tasks.example.com/api/tasks/submit
  auth_secret_ref: env:EXTERNAL_TASK_API_TOKEN
  idempotency_key: project_id:external_task_id:attempt_index
  max_outbox_attempts: 10
```


## 14. 接口设计

### 14.1 CLI

建议命令：

- `annotation-pipeline init`
- `annotation-pipeline create-tasks`
- `annotation-pipeline run`
- `annotation-pipeline retry --task-id ...`
- `annotation-pipeline inspect --task-id ...`
- `annotation-pipeline merge --task-id ...`
- `annotation-pipeline dashboard build`
- `annotation-pipeline dashboard serve`
- `annotation-pipeline settings validate`
- `annotation-pipeline settings set-route --stage annotation ...`
- `annotation-pipeline annotators add ...`
- `annotation-pipeline annotators select --task-id ... --annotator-id ...`
- `annotation-pipeline preview render --task-id ...`
- `annotation-pipeline human-review decide --task-id ... --decision accept`
- `annotation-pipeline feedback decide --task-id ... --feedback-id ... --decision annotator_rerun`
- `annotation-pipeline providers test --provider-id ...`
- `annotation-pipeline external pull --limit ...`
- `annotation-pipeline external drain-outbox`
- `annotation-pipeline doctor`

### 14.2 API

MVP 需要只读、少量控制、settings 和外部任务接入接口：

- `GET /health`
- `GET /dashboard`
- `GET /tasks/<task_id>`
- `GET /settings`
- `POST /settings/validate`
- `GET /providers`
- `POST /providers/test`
- `POST /tasks/<task_id>/retry`
- `POST /tasks/<task_id>/approve`
- `POST /tasks/<task_id>/reject`
- `POST /tasks/<task_id>/merge`
- `POST /tasks/<task_id>/start`
- `POST /tasks/<task_id>/stop`
- `GET /annotators`
- `POST /tasks/<task_id>/annotator`
- `GET /tasks/<task_id>/preview`
- `POST /tasks/<task_id>/preview/render`
- `POST /tasks/<task_id>/human-review/decision`
- `GET /tasks/<task_id>/feedback`
- `POST /tasks/<task_id>/feedback/<feedback_id>/decision`
- `POST /external/tasks/pull`
- `POST /external/tasks/status`

API 设计原则：

- 所有写接口都必须返回 audit event id。
- 写接口不能绕过 application service。
- MVP 中 `GET /settings`、`POST /settings/validate`、`GET /providers`、`GET /annotators` 是只读/校验接口；UI 不写 YAML 配置。
- 外部 task status endpoint 用于 webhook 或 adapter 测试，不作为 core 状态真相。
- MVP 不提供 webhook ingestion endpoint；外部任务进入系统必须通过 pull 或本地 import。
- dashboard API 返回 read model，其中 worker live counts 必须标明来源：runtime、snapshot 或 fallback。

### 14.3 Web Dashboard

默认 Web 看板必须使用 TypeScript Web 框架实现，推荐 React 系生态。主界面采用 Kanban-first 布局，后端仍由 Python API 提供 read model 和控制接口；前端只依赖 HTTP API，不读取 task store 文件，也不直接调用 provider。

- Sidebar：runtime health、heartbeat age、service state、并发设置、自动派发开关、provider route form、刷新按钮
- Summary：task 总数、ready/pending、active、live workers、done/merged
- Kanban board：按 pipeline stage 分列展示 task card，过滤搜索后仍保留列结构
- Default columns：Ready、Annotating、Validating、QC、Human Review、Repair、Accepted、Rejected、Merged
- Task card：status badge、task id、source/ref、row range 或 slice summary、runtime parts、retry time、QC history、错误摘要、start/stop/detail 控件
- Detail drawer：点击 task card 后打开右侧抽屉，承载 attempts、events、artifacts、feedback、provider route、annotator、preview 和 Human Review 控件
- Human Review：图片 bbox overlay、视频帧 overlay、点云 viewer 状态和 accept/reject/request-repair 控件
- Annotator：当前 annotator、可用 capability、fallback、recent quality metrics
- Filters：source、status、task id/external id 搜索
- Settings：阶段级 primary/fallback provider、model、effort、pause reason

前端实现要求：

- 使用 TypeScript 定义 dashboard、task detail、settings、provider route 和 action response 类型。
- API client 集中在 `web/src/api/`，不要在组件里散落 `fetch` 调用。
- 写操作必须使用后端返回的 audit event id 更新 UI 状态或触发重新拉取。
- provider secret 只显示引用状态，不能在前端 payload、local storage 或日志中出现明文。
- 控制动作必须有 disabled/loading/error 状态，避免重复提交。


## 15. 观测性

### 15.1 日志

分三类：

- operator log
- task event log
- worker execution log

### 15.2 Metrics

建议采集：

- tasks by status
- average stage duration
- validation failure rate
- qc failure rate
- feedback count by severity, code, source, and repair decision
- bulk repair success rate
- annotator rerun success rate after feedback
- retry count distribution
- merge success rate

### 15.3 Dashboard Snapshot

Dashboard 不直接重扫全部 artifacts，而是消费聚合 snapshot，降低开销并减少状态抖动。


## 16. 安全与隔离

### 16.1 Worker 隔离

即使默认 runtime 是 local subprocess，也应支持：

- 独立工作目录
- 独立临时目录
- 限制可写路径
- 最小权限 provider config 注入

### 16.2 密钥管理

- provider token 不写入 task state
- token 通过环境变量或 secret provider 注入
- artifacts 中避免落私密原始凭据

### 16.3 审计要求

- 所有人工 override 都应留下 audit event
- 所有自动 repair 都应保留输入依据


## 17. 测试策略

### 17.1 单元测试

覆盖：

- 状态机转换
- store 原子写入
- retry policy
- validator contract
- adapter contract
- settings validation
- provider route selection
- external task outbox behavior

### 17.2 集成测试

覆盖：

- create task -> annotate -> validate -> qc -> merge
- worker crash recovery
- retry scheduling
- merge failure recovery
- dashboard read model with runtime overlay refresh
- external task pull -> process -> status/result submission

### 17.3 合约测试

针对插件接口：

- DatasetAdapter contract tests
- Validator contract tests
- RuntimeBackend contract tests
- ProviderClient contract tests
- ExternalTaskAdapter contract tests

### 17.4 示例测试

仓库内 demo 项目必须在 CI 中可完整跑通。


## 18. 迁移与演进策略

### 18.1 从项目专用实现迁移到框架

迁移顺序建议：

1. 先定义 core models 和 plugin contracts
2. 再实现 file store + local runtime
3. 再把项目现有 validator、prompt builder、merge sink 包成 adapter
4. 最后按需接入 queued runtime 或 web UI

### 18.2 向后兼容原则

- 核心状态模型稳定优先
- 插件接口版本化
- runtime backend 可替换但不侵入 domain model


## 19. 推荐技术选型

### MVP

- Python 3.11+
- `pydantic` 或 dataclass + explicit validation
- 文件系统 store
- subprocess runtime
- Typer 或 argparse CLI
- FastAPI 或最小 HTTP server 提供 API
- TypeScript + React 系框架实现 Web dashboard

### 可选增强

- Redis 作为 queue backend
- SQLite/Postgres 作为 store backend
- Next.js、Vite React 或同等级 TypeScript 框架做更完整的 dashboard


## 20. 最终架构结论

`annotation-pipeline-skill` 的正确技术方向不是“把现有大脚本开源”，而是：

- 用清晰的核心领域模型重建任务系统
- 用插件接口隔离数据集和任务特定逻辑
- 用可替换 runtime 支持从单机到队列化部署
- 用 deterministic gate、QC、repair、merge 组成标准流水线

最终应形成一个“框架内核稳定、业务适配可插拔、默认本地可跑、重型部署可扩展”的开源 skill。
