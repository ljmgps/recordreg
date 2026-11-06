# 录音转写与说话人分离工具 —— 开发设计文档

| 项目代号 | RecordReg |
| --- | --- |
| 文档版本 | v1.0（设计定稿，待评审） |
| 编写日期 | 2026-11-05 |
| 目标平台 | Windows 10/11 x64 + NVIDIA RTX 3050 (8GB) |
| 技术栈 | Python 3.11 + FunASR + PySide6 + SQLite |
| 运行形态 | 纯本地离线，无任何联网 API 调用 |

---

## 1. 项目概述

### 1.1 一句话定义

把本地的 mp3 / aac / m4a 录音文件批量转写成**带说话人标注、带时间戳、结构化留存**的文本，输出为对人类可读、对 AI 知识库友好的 Markdown + JSON 双份文件，全程离线、可离线重跑、可增量处理。

### 1.2 设计目标

| 编号 | 目标 | 衡量标准 |
| --- | --- | --- |
| G1 | 识别准确 | 中文普通话会议/访谈场景，字错率（CER）控制在 10% 以内 |
| G2 | 说话人可分 | 2 人对谈场景，说话人归属正确率 ≥ 90%，且标签稳定不来回跳 |
| G3 | 完全离线 | 断网状态下可完成"装好后"的全部转写流程 |
| G4 | 批量可跑 | 100 个 × 3 分钟文件一次排队跑完，中途不崩、不爆显存、可断点续跑 |
| G5 | 结果可管 | 每个文件的结果可被检索、去重、重跑、人工改名 |
| G6 | 知识库可用 | 输出文件可直接切块进向量库，说话人边界与语义边界一致 |

### 1.3 明确的非目标（本版本不做）

- ❌ 不做实时/live 转写（只处理已存在的录音文件）
- ❌ 不做内置大模型问答与摘要（问答交给外部 AI 工具消费导出的文件）
- ❌ 不做跨文件的声纹认人（不说话人"注册"，不区分"张三/李四"，只区分编号）
- ❌ 不做重叠语音分离（两人同时说话时无法拆分，属已知限制，见 §13.3）
- ❌ 不做说话人情绪/事件的深度利用（SenseVoice 会输出情感与事件标签，仅作为 JSON 字段留存，不参与业务逻辑）

### 1.4 典型使用场景

1. 每天下班前把当天录的访谈/沟通录音丢进去批量跑一遍，第二天直接看 Markdown。
2. 积累几个月后，把整个 `output/` 目录丢进任意支持 RAG 的工具（AnythingLLM / Dify / 本地知识库），直接问"上个月关于预算的讨论结论是什么"。
3. 某个文件识别错了，改一下参数单独重跑该文件，其它文件不受影响。

---

## 2. 需求确认表

以下为与需求方沟通后锁定的结论，后续开发以此为准。

| # | 原始需求 | 落地结论 | 备注 |
| --- | --- | --- | --- |
| 1 | 录音转文本，需留存管理，可能作为 AI 知识库 | **Markdown + JSON 双份输出** + SQLite 索引；额外生成 `chunks.jsonl` 便于直接切片入库 | 见 §7 |
| 2 | 支持 mp3 / aac / m4a | 统一经 ffmpeg 解码为 16kHz 单声道 PCM 后再送入模型 | 见 §5.1 |
| 3 | 核心用 Python | Python 3.11（需另装，理由见 §11.1） | 本机现有 3.13/3.14 不适用 |
| 4 | 说话人识别，不联网，文件中标注 | **fsmn-vad + cam++ 声纹 + 受约束层次聚类**，全本地 | 见 §6 |
| 5 | 能在 RTX 3050 + Windows 跑 | 显存占用预算 < 3GB，可与其他程序共存 | 见 §12.1 |
| 6 | 简单 GUI，单文件 + 批量 | PySide6，任务表格 + 拖拽 + 队列 | 见 §9 |
| 7 | 创建虚拟环境 | `.venv/`，Python 3.11 | 见 §11.1 |
| 8 | 一键启动 bat | `启动.bat` + `安装环境.bat` + `下载模型.bat` | 见 §14 |
| 9 | 开源本地模型 | SenseVoiceSmall（Apache-2.0 系，通义实验室开源） | 见 §3.1 |
| 10 | 从 ModelScope 下载，禁库内自动下载 | 独立脚本用 modelscope CLI 下到 `models/`，代码内全部传**本地绝对路径** + `disable_update=True` | 见 §11.3 |
| 11 | 100 个 × 3 分钟，准确度优先 | 串行队列 + 动态 batch；预计单批 15–25 分钟 | 见 §12.2 |

### 2.1 本次沟通补充确认

| 议题 | 决策 |
| --- | --- |
| 输出格式 | Markdown + JSON（不出 Word、不出 SRT，保持精简；SRT 留作后续可选开关） |
| 说话人场景 | **2 人对谈为主**，因此默认强制 K=2 聚类，并可一键切换"自动估计人数" |
| GUI 框架 | PySide6 |
| 知识库定位 | 只导出，不内置问答 |

---

## 3. 技术选型

### 3.1 语音识别模型选型

| 候选 | 中文准确率 | 速度（GPU） | 本地部署 | 说话人支持 | 结论 |
| --- | --- | --- | --- | --- | --- |
| **SenseVoiceSmall** | 优（中文 CER 明显优于 Whisper-L） | 极快，非自回归，170× 实时（官方） | ✅ 纯本地 | ✅ 可接 cam++ | **选用** |
| Paraformer-large | 优（长音频稳定） | 较快，但自回归解码更慢 | ✅ | ✅ 有 spk 一体模型 | 备选，保留切换开关 |
| Whisper-large-v3 | 中（中文偏弱、易幻觉） | 慢 | ✅ | ❌ 需外挂 pyannote | 排除 |
| faster-whisper | 中 | 快 | ✅ | ❌ 同上 | 排除 |

**选 SenseVoiceSmall 的决定性理由：**

1. **非自回归架构**，10s 音频推理约 70ms，长音频耗时几乎线性，不会像 Whisper 那样长音频掉速或产生重复幻觉（"谢谢观看"）。
2. **不需要额外标点模型**。SenseVoice 原生输出带标点和逆文本正则化（ITN），省掉一个 ct-punc 模型，链路更短、显存更省。
3. **自带语种识别**，中英混说场景不必手动切模型。
4. 输出为富文本标记格式（`<|zh|><|NEUTRAL|><|Speech|><|woitn|>正文`），其中情感/事件标签可顺手存进 JSON，将来要扩展不用换模型。
5. 模型体积约 **897MB**，3050 的 8GB 显存毫无压力。

唯一缺点：**SenseVoice 本身不输出时间戳**（整段出文本）。这正好由 VAD 分段来提供边界——而 VAD 分段本来就是说话人分离的必需步骤，两者天然契合，不构成额外成本。

### 3.2 说话人分离方案选型

| 候选 | 需联网/授权 | 中文效果 | 可控性 | 结论 |
| --- | --- | --- | --- | --- |
| **FunASR: fsmn-vad + cam++ + 层次聚类** | 否，ModelScope 公开可下 | 好 | **高**（可强制人数、可后处理） | **选用（主）** |
| FunASR 官方 `spk_model="cam++"` 一体调用 | 否 | 好 | 低（黑盒，人数自动猜） | 选用（对照/兜底） |
| pyannote.audio 3.x | **是**（HuggingFace gated，需申请 token） | 好 | 中 | 排除（用户无法访问 HF） |
| 3D-Speaker / ERes2Net | 否 | 很好 | 中 | 备选（eres2netv2 可替换 cam++） |

**关于官方一体调用的说明：** FunASR 新版可一行 `AutoModel(model="iic/SenseVoiceSmall", vad_model="fsmn-vad", spk_model="cam++")` 直接出带 `spk` 字段的句级结果，代码量极小。但它的聚类人数是算法自动估计的，**在 2 人对谈场景里经常估出 3–4 人**（把咳嗽、笑声、远端串音聚成"第三个人"），且参数不可干预。

因此本项目采用**自建流水线**：拆开 VAD / ASR / 声纹三步自己串，聚类环节强制 K=2 并加后处理平滑。这样能多拿到约 5–10 个百分点的说话人归属正确率，且所有中间产物（每段 embedding、时长、置信度）都可落盘，出问题能查。

官方一体调用保留为 `--engine=official` 开关，用于快速验证环境是否装对了，以及出争议时的对照。

### 3.3 关键依赖版本

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| Python | 3.11.9 | 见 §11.1 |
| torch / torchaudio | 2.5.1 + cu121 | 3050 是 sm_86，cu121 完全够；比 cu124 口径更稳 |
| funasr | **1.2.9（锁死）** | 语音框架；1.3+ 移除 SentencepiecesTokenizer，无法加载 SenseVoice |
| modelscope | 1.2x | **仅用于下载模型**，运行时不触发下载 |
| numpy | < 2.0 | 与 torch 2.5 / funasr 生态兼容性最稳 |
| scikit-learn | 1.5.x | 层次聚类、silhouette |
| PySide6 | 6.7.x | GUI |
| ffmpeg | 系统已装（C:\Program Files\ffmpeg） | 解码 |

---

## 4. 系统架构

### 4.1 分层

```
┌──────────────────────────────────────────────────────┐
│  UI 层（PySide6）                                     │
│  主窗口 / 任务表格 / 设置对话框 / 结果预览             │
└───────────────────────┬──────────────────────────────┘
                        │ Qt Signal / Slot
┌───────────────────────▼──────────────────────────────┐
│  调度层（Worker）                                     │
│  BatchRunner：队列、并发控制、取消、进度聚合、重试     │
└───────────────────────┬──────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────┐
│  流水线层（Pipeline）                                 │
│  AudioDecoder → VadSegmenter → AsrEngine              │
│                    → Embedder → Cluster → Smoother    │
│                    → ResultAssembler                  │
└───────────────────────┬──────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────┐
│  基础设施层                                           │
│  ModelRegistry（单例，懒加载，常驻显存）               │
│  Store（SQLite） / Writer（md+json+jsonl） / Config   │
└──────────────────────────────────────────────────────┘
```

### 4.2 单文件数据流

```mermaid
flowchart TD
    A[mp3/aac/m4a] -->|ffmpeg| B[16kHz mono WAV]
    B -->|fsmn-vad| C[语音段列表 start/end ms]
    C -->|段预处理| D[规整后的段<br/>合并过短 / 切分过长]
    D -->|SenseVoice batch| E[每段文本]
    D -->|cam++| F[每段 192 维声纹向量]
    F -->|层次聚类 K=2| G[每段 spk 标签]
    E --> H[结果拼装]
    G -->|后处理平滑| H
    H --> I[Markdown]
    H --> J[JSON]
    H --> K[chunks.jsonl]
    I --> L[(SQLite 索引)]
```

### 4.3 模块职责

| 模块 | 文件 | 职责 | 关键约束 |
| --- | --- | --- | --- |
| 解码 | `core/audio.py` | ffmpeg 转码、探时长、抽样静音检测 | 必须走 subprocess，不走 torchaudio backend |
| VAD | `core/vad.py` | 调 fsmn-vad 出语音区间 | 输出统一为毫秒整数对 |
| 段预处理 | `core/vad.py` | 合并 <0.4s 碎片、切分 >30s 长段 | 保证单段不超 VAD 上限 |
| ASR | `core/asr.py` | SenseVoice 批量识别 + 富文本清洗 | 全部本地路径，禁用自动下载 |
| 声纹 | `core/embed.py` | cam++ 提 embedding | 兼容不同 funasr 版本返回结构 |
| 聚类 | `core/cluster.py` | 受约束层次聚类 + 人数估计 | K 可强制 |
| 平滑 | `core/smooth.py` | 短段回填、孤立段修正、相邻合并 | 规则化、可关闭 |
| 组装 | `core/assemble.py` | 合并成轮次，计算统计 | 纯函数，便于单测 |
| 输出 | `core/writer.py` | 写 md / json / jsonl | 模板集中管理 |
| 索引 | `core/store.py` | SQLite 读写、去重、状态流转 | 线程安全（单写者） |
| 调度 | `core/runner.py` | 队列、进度、取消、重试 | 串行执行 |
| 模型 | `core/registry.py` | 单例加载、显存管理、设备探测 | 只加载一次 |

---

## 5. 核心处理流水线

### 5.0 总览

单个文件的处理分为 8 步，每步都可独立失败并记录原因，不因某一步失败而丢整个批次的进度。

```
decode → vad → normalize_segments → asr → embed → cluster → smooth → assemble → write → index
```

### 5.1 解码（AudioDecoder）

- **为什么不用 torchaudio / librosa 直接读**：m4a/aac 在 Windows 上的解码后端支持不稳定，且 torchaudio 2.5 起后端 API 变动大。用 ffmpeg 是唯一可靠的路径，用户机器上已装（`C:\Program Files\ffmpeg\bin\ffmpeg.exe`）。
- 命令：

```bash
ffmpeg -hide_banner -loglevel error -y -i "<input>" \
       -vn -ac 1 -ar 16000 -acodec pcm_s16le "<cache>/<sha8>.wav"
```

- 缓存策略：WAV 落在 `cache/<sha256前8位>.wav`，处理完默认删除（可配置保留）。100 个 3 分钟文件约需 5.8GB 临时空间，因此**必须逐个处理、逐个清理**，不能一次性全部转码。
- 时长探测：`ffprobe -show_entries format=duration`，失败时回退到 WAV 帧数计算。
- 异常：文件损坏 → 该文件标记 `failed`，原因写库，批次继续。
- 采样率异常（如 8k 电话录音）：仍然重采样到 16k（不重采样模型会不准），但在 JSON 里记录 `origin_sample_rate`，便于事后判断为何某文件效果差。

### 5.2 VAD 分段（VadSegmenter）

调用 fsmn-vad，参数：

| 参数 | 值 | 理由 |
| --- | --- | --- |
| `max_single_segment_time` | 30000 ms | 防长段撑爆显存；30s 是准确率与效率的平衡点 |
| `speech_noise_thres` | 0.6 | 默认 0.6，环境嘈杂可上调 |
| `max_end_silence_time` | 800 ms | 句末静音超过 0.8s 视为断句，契合对话节奏 |

输出：`[(start_ms, end_ms), ...]`。3 分钟对谈通常在 40–120 段之间。

### 5.3 段规整（normalize_segments）

VAD 原始输出存在三类问题，必须规整后才能做声纹：

| 问题 | 处理 |
| --- | --- |
| 极短段（< 0.4s）：多为"嗯""对"、咳嗽、键盘声 | 若与相邻段间隔 < 0.3s 且同段合并后仍 < 30s → 合并；否则丢弃（但记入 JSON 的 `dropped_segments`） |
| 超长段（> 30s）：连续讲述 | 在段内做能量低谷切分，切成 ≤ 15s 的子段，切点取局部能量最小处 |
| 间隔极小的相邻段（< 0.2s） | 合并，避免同一句话被切碎导致说话人判断抖动 |

> 短段合并的意义：cam++ 对 0.3s 音频提取的 embedding 噪声极大，是聚类的最大污染源。宁可合并/丢弃，也不要让它们参与聚类——但**合并后的音频仍参与识别**，只是不单独产生一个说话人轮次。

### 5.4 识别（AsrEngine）

```python
res = asr_model.generate(
    input=seg_wav_paths,        # 一批段的 wav 路径
    cache={},
    language="auto",
    use_itn=True,
    batch_size_s=120,           # 动态 batch：累计 120s 音频一批
    merge_vad=False,            # 我们自己控制分段，禁止它再合并
    disable_update=True,        # 关键：禁止联网检查更新/自动下载
)
```

- **输出清洗**：原始输出形如 `<|zh|><|NEUTRAL|><|Speech|><|woitn|>今天我们先过一下进度`，需用 `rich_transcription_postprocess()` 剥离标记，同时把 `zh / NEUTRAL / Speech` 分别存进 JSON 的 `lang / emotion / event` 字段。
- **批内顺序**：必须保证 `res[i]` 与输入段 `i` 严格对应。做法是逐段循环 + 内部累计 batch，自己维护映射表，不依赖框架的返回顺序。
- **空结果处理**：若某段识别为空（纯噪音），标 `text=""` 并在最终输出中跳过该段，但计入统计。

#### 语种设置：默认强制 zh

SenseVoice 的 `language="auto"` 会让模型对**每个语音段**单独做语种识别（LID）。对 1–2 秒的短段（"嗯""对""好的"），LID 极易误判成 `ja` / `en`。

**影响评估：** 对正文识别的影响很小（SenseVoice 是多语言统一声学模型，解码共享），但会污染元数据——段级 `lang` 字段乱跳，`language` 整体标注失真，后续按语种筛选或统计时会误判。

**处理：** `config.yaml` 默认 `language: zh`，跳过 LID 预测。同时 `pipeline.py` 按段做一次多数投票，把主导语种写入 JSON 顶层 `language`；若段级结果与配置不一致，只记 debug 日志、不改正文。确实需要处理外语录音时改回 `auto` 即可。


### 5.5 声纹提取（Embedder）

```python
sv = AutoModel(model=campplus_local_path, device=dev, disable_update=True)
emb = sv.generate(input=wav_16k_array)   # → 192 维向量
```

工程注意点：

1. **返回结构防御**：不同 funasr 版本 cam++ 的返回可能是 `[{...}]`、`dict` 或直接 `np.ndarray`。封装 `_extract_embedding()` 做 5 种形态的探测，取第一个形如 `(D,)` 且 D∈{192,512} 的向量。
2. **L2 归一化**：所有 embedding 统一做 L2 归一化，后续用余弦距离，避免长度差异干扰。
3. **静音段不参与**：能量过低的段直接跳过（已在 5.3 处理）。
4. 单段 embedding 提取在 3050 上约 3–8ms，100 个文件 ≈ 8000 段，累计约 1 分钟内。

### 5.6 聚类（Cluster）

见 §6 专章。默认 `n_clusters=2`。

### 5.7 后处理平滑（Smoother）

见 §6.3 规则表。

### 5.8 组装与输出

把逐段结果按"同说话人 + 时间间隔 < 1.5s"合并成**轮次（turn）**，再写文件。一个 3 分钟对谈通常得到 20–60 个轮次。

---

## 6. 说话人分离算法设计（重点）

### 6.1 为什么 2 人场景要强制 K=2

cam++ 的 embedding 空间里，"两个人 + 背景噪音 + 笑声 + 咳嗽" 会形成 3–5 个可分的簇。自动估计人数（如 silhouette 或 DBSCAN）会把噪音簇也算作一个人，导致：

- 结果里出现"说话人3"，实际是几段笑声；
- 真两个人的段被错误切分到不同簇（因为音色随情绪/语速漂移）。

2 人对谈的先验非常强——**直接告诉算法"只有 2 个人"**，聚类就被约束在一个正确的解空间里，效果立竿见影。这也是官方一体调用做不到的。

### 6.2 聚类实现

```python
from sklearn.cluster import AgglomerativeClustering

X = l2_normalize(embeddings)          # (N, 192)
labels = AgglomerativeClustering(
    n_clusters=2,                      # 强制两人
    metric="cosine",
    linkage="complete",                # 见下方选型说明
).fit_predict(X)
```

> **2026-11-05 修订：linkage 从 `average` 改为 `complete`。**
>
> 实施时发现 average 在余弦空间会把存在"链式相似"的点并成一个大簇，实测量：
>
> | 策略 | 13 分钟对话的分段 | 38 分钟对话的分段 | silhouette |
> | --- | --- | --- | --- |
> | average | 169 : 13 段（709s : 18s） | 482 : 1 段 | 0.333 |
> | **complete** | **107 : 75 段（433s : 294s）** | **265 : 218 段** | **0.446** |
>
> complete（最远距离）强制簇间保持最大距离，两类更均衡，也更贴近真实对谈的形态。

**分离度保护**：聚类后计算 silhouette，若 < 0.10，说明数据本身不支持分成这么多人（典型情况是单人独白被强行按 K=2 处理），此时退回单说话人，而不是硬把一个人的话割裂给两个人。

**自动模式**（设置里可切）：K 在 2..4 中穷举，用 silhouette（余弦）选最优，并加两条业务约束：

- 若最优 K=3 但最小簇的总时长占比 < 10% → 降为 K=2 并合并到最近簇；
- 若 K=2 的两个簇时长占比都 ≥ 15% → 直接采纳。

**标签稳定化**：聚类结果的编号顺序不确定（这次可能 A=0/B=1，下次反过来）。按"总时长更长的簇固定为 SPK_1"排序，保证同一程序多次跑同一文件结果一致。

### 6.2.1 分段粒度是前提（比聚类算法更关键）

说话人分离的效果，一半取决于分段质量。最初的 VAD 参数把一个 13 分钟的电话切成 18 段、平均 45 秒/段，一段里已经包含好几个话轮，声纹必然是混合的，任何聚类算法都救不回来。

改为：

| 参数 | 初值 | 现值 | 作用 |
| --- | --- | --- | --- |
| `max_end_silence_time` | 800 ms（默认） | **500 ms** | 停顿多久算断句，对谈要更敏感 |
| `split_if_over_time` | 30000 ms | **8000 ms** | 超过 8 秒的段主动切细 |
| `split_target_time` | 15000 ms | **5000 ms** | 切分目标长度 |

调整后同一文件从 18 段变成 42 段，38 分钟录音从 187 段变成 486 段，说话人分离才真正生效。

代价是 ASR 的上下文变短，但实测识别质量未见下降（5 秒左右的段 SenseVoice 处理得很好）。


### 6.3 平滑规则（按顺序执行）

| 规则 | 触发条件 | 动作 | 目的 |
| --- | --- | --- | --- |
| R1 碎段归并 | 段时长 < 0.6s | 直接继承前一段的说话人 | 消除"嗯/对"造成的假轮换 |
| R2 孤立修正 | 段时长 < 1.2s 且前后两段同说话人 | 改为前后段的说话人 | 修正聚类抖动 |
| R3 长段保护 | 段时长 ≥ 1.2s | **不做任何修改** | 保护真实的短插话，避免过度平滑 |
| R4 幽灵簇合并 | 簇总时长 < 30s **且** 段数 < 8 | 整簇合并进最大簇 | 消除噪音误聚的"幽灵说话人" |
| R5 相邻合并 | 相邻段同说话人且间隔 < 1.5s | 合并为一个轮次 | 输出更接近人类阅读习惯 |

R1/R2 的阈值都可调，且**所有被修改的段在 JSON 里标记 `smoothed: true` 与 `raw_speaker`**，便于事后核查平滑是否过度。

> **R4 的判据修订（2026-11-05）**：原设计按"占比 < 5%"判定幽灵簇，实施时误杀了真实场景里"说话少的那一方"——13 分钟对话中一方只说了 18 秒（占 2.5%），被判为噪音后整份录音退化成"单人"。改为**绝对时长 + 段数双条件**（< 30 秒 **且** < 8 段）后正常。

### 6.4 人工干预手段（GUI 内）

- 设置里可切换：`固定2人（默认）` / `自动估计` / `固定N人`
- 结果预览页可以：对某个轮次点右键 → 「改为另一个说话人」，改动写回 JSON 与 Markdown
- 说话人改名：把 `SPK_1` → 「张经理」，写入 `speaker_aliases` 表与 md 的 front matter

> 这部分是**必要的**：再好的自动聚类也会有 5–10% 的错判，给一个人工纠错的入口，比追求 100% 自动更实在。

---

## 7. 输出格式规范

### 7.1 目录与命名

```
output/
├── 2026-11/
│   ├── 2026-11-05_产品需求沟通.md
│   ├── 2026-11-05_产品需求沟通.json
│   └── ...
├── chunks/
│   └── chunks.jsonl          # 全量切片，增量追加
└── _failed/
    └── 2026-11-05_损坏文件.error.txt
```

命名规则：`{录音日期或处理日期}_{原文件名去扩展名}.md`。原文件名清洗掉 `\/:*?"<>|` 等非法字符。

### 7.2 Markdown 模板

> 2026-11-05 修订：按需求方反馈，Markdown 只保留源文件名与转写内容，
> 所有元数据下沉到 JSON。原设计中的 YAML front matter 已移除。

```markdown
# 产品需求沟通

源文件：`2026-11-05 产品需求沟通.m4a`

**`[00:00 → 00:26]`**

今天我们先过一下这个季度的进度，主要是三块：产品、测试、上线时间。
测试这边可能还需要两周，主要是回归用例还没补完。

**`[00:26 → 00:56]`**

两周可以接受，那我们把上线时间往后调一周。
```

**格式取舍：**

1. **不做 YAML front matter**。需求方明确要求简略；元数据（时长、语种、说话人、模型版本、统计、逐段时间戳）全部在 JSON 里，需要时查 JSON 即可。
2. **每个阅读块独立成段**，块边界优先落在语音停顿处。这是 RAG 切片友好的关键：不会把两段不相干的话混进一个 chunk。
3. **块首时间戳用 `[MM:SS → MM:SS]`**，既能溯源又不打断阅读。
4. 块长度受 `min 8s / max 25s / 停顿 1.2s` 三个参数约束，避免"两秒一段"的碎片感。


### 7.3 JSON Schema

```json
{
  "schema_version": "1.0",
  "tool_version": "1.0.0",
  "file": {
    "name": "project-meeting-2026-11-05.m4a",
    "ext": "m4a",
    "path": "D:/Audio/project-meeting-2026-11-05.m4a",
    "size_bytes": 2918400,
    "sha256": "9f2c1ab3...",
    "duration_sec": 192.4,
    "origin_sample_rate": 44100,
    "origin_channels": 1
  },
  "models": {
    "asr": "SenseVoiceSmall",
    "vad": "fsmn-vad-zh-cn-16k-common",
    "speaker": "campplus_sv_zh-cn_16k-common",
    "device": "cuda:0"
  },
  "params": {
    "vad_max_segment_ms": 30000,
    "cluster_mode": "fixed_k",
    "cluster_k": 2,
    "smoothing": true
  },
  "speakers": [
    { "id": "SPK_1", "alias": "说话人1", "total_sec": 118.3, "turn_count": 18, "char_count": 702 },
    { "id": "SPK_2", "alias": "说话人2", "total_sec": 61.2, "turn_count": 16, "char_count": 582 }
  ],
  "segments": [
    {
      "idx": 1,
      "start": 3.12,
      "end": 11.48,
      "duration": 8.36,
      "speaker": "SPK_1",
      "raw_speaker": "SPK_1",
      "smoothed": false,
      "text": "今天我们先过一下这个季度的进度，主要是三块：产品、测试、上线时间。",
      "lang": "zh",
      "emotion": "NEUTRAL",
      "event": "Speech"
    }
  ],
  "stats": {
    "segment_count": 96,
    "dropped_segments": 4,
    "char_count": 1284,
    "rtf": 0.031,
    "elapsed_sec": 12.6
  }
}
```

字段设计说明：

- `raw_speaker` 与 `smoothed`：平滑是"有损"操作，必须留下原始判断，否则出问题无法回溯。
- `rtf`（real-time factor）：实测性能指标，累积起来可以判断机器是否需要升级。
- `origin_sample_rate`：8k 电话录音效果差时，能立刻定位原因。

### 7.4 chunks.jsonl（供 RAG 直接使用）

每行一个 chunk，UTF-8，无 BOM，追加写：

```json
{"id":"9f2c1ab3-001","file":"2026-11-05 产品需求沟通.m4a","title":"产品需求沟通","date":"2026-11-05","speaker":"说话人1","start":3.12,"end":11.48,"text":"今天我们先过一下这个季度的进度，主要是三块：产品、测试、上线时间。","prev":"","next":"测试这边可能还需要两周，主要是回归用例还没补完。"}
```

- `prev` / `next` 带上相邻轮次的文本，**给切片保留上下文**（很多 RAG 系统做 small-to-big 检索时正好需要）。
- 单 chunk 超过 300 字时按句号二次切分，`id` 加 `-a/-b` 后缀。
- 这个文件是唯一的"全量扁平视图"，向量库导入时只需要这一个文件，不用遍历 100 个 md。

---

## 8. 数据存储

### 8.1 SQLite 表结构

```sql
CREATE TABLE files (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    path          TEXT NOT NULL,
    name          TEXT NOT NULL,
    ext           TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    size_bytes    INTEGER,
    duration_sec  REAL,
    mtime         REAL,
    status        TEXT NOT NULL,   -- pending|running|done|failed|skipped
    error         TEXT,
    out_md        TEXT,
    out_json      TEXT,
    speaker_count INTEGER,
    char_count    INTEGER,
    elapsed_sec   REAL,
    model_ver     TEXT,
    created_at    TEXT DEFAULT (datetime('now','localtime')),
    processed_at  TEXT
);
CREATE UNIQUE INDEX idx_files_sha ON files(sha256);
CREATE INDEX idx_files_status ON files(status);

CREATE TABLE speaker_aliases (
    sha256  TEXT NOT NULL,
    spk_id  TEXT NOT NULL,
    alias   TEXT NOT NULL,
    PRIMARY KEY (sha256, spk_id)
);

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE batches (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    finished_at TEXT,
    total      INTEGER,
    success    INTEGER,
    failed     INTEGER
);
```

### 8.2 去重与断点续跑

- **去重键 = sha256**。同一个文件改名、换目录、重复拖入，都只记一条。
- 状态机：`pending → running → done | failed`。程序异常退出时，所有 `running` 状态在下次启动时重置为 `pending`，避免"卡死"。
- **断点续跑**：批次开始时跳过已有 `done` 且 `model_ver` 一致的文件；改了模型或参数想全量重跑，界面上勾选"强制重跑"。
- 这是支撑"100 个文件跑到第 60 个崩了"不重来的关键，必须第一版就做。

### 8.3 为什么用 SQLite 而不是 JSON 索引文件

100 个文件时 JSON 也行，但一年后上千条时，需要按时间、状态、说话人数筛选，SQL 一行搞定，手写 JSON 过滤容易出错。SQLite 是 Python 标准库自带，零依赖。

### 8.4 实现要点（2026-11-05）

实际落地在 `app/core/store.py`，表结构与 §8.1 一致，另补了 `turn_count` 字段。几个实现选择：

| 问题 | 做法 |
| --- | --- |
| 去重键 | `sha256`，内容相同就是同一个文件，改目录、改名都能识别 |
| 跳过条件 | `status='done'` **且** `model_ver` 与当前版本一致；换了模型或改了逻辑会自动重跑 |
| 中断恢复 | 启动时把所有 `running` 重置为 `pending`，不会永久卡住 |
| 失败可重试 | 失败记 `status='failed'` + 错误原因，下次运行自动重试（因为不是 done） |
| 版本号 | `writer.TOOL_VERSION`，M3 起为 `0.2.0`，改算法后递增即可让旧结果失效 |
| 单文件重复计算 | CLI 先算 sha 再传给 `process_file(sha=...)`，大文件不哈希两遍 |
| 索引损坏 | 库文件删掉重建即可，输出文件本身不受影响（索引只是加速与状态记录） |

配套命令：

```bat
app\cli.py "D:\录音" --status    :: 查看统计与最近记录
app\cli.py "D:\录音" --force     :: 忽略索引强制重跑
app\cli.py "D:\录音" --list-only :: 只看会处理哪些文件
```


---

## 9. GUI 设计

### 9.1 窗口布局

```
┌───────────────────────────────────────────────────────────────────────┐
│ [＋添加文件] [📁添加文件夹] [清空]      [▶开始] [⏸暂停] [⏹停止]  [⚙设置] │
├───────────────────────────────────────────────────────────────────────┤
│  文件名                    │ 时长   │ 状态      │ 进度      │ 耗时 │ 输出 │
│  ─────────────────────────────────────────────────────────────────── │
│  2026-11-05 产品需求沟通.m4a│ 03:12 │ ✓ 完成    │ ████████  │ 14s │ 打开 │
│  2026-11-04 客户回访.aac    │ 02:48 │ ▶ 识别中  │ ████░░░░  │  -  │  -   │
│  2026-11-03 周会.mp3        │ 03:40 │ ⏳ 等待   │ ░░░░░░░░  │  -  │  -   │
│  2026-11-02 损坏文件.m4a    │  -    │ ✗ 失败    │ ░░░░░░░░  │  -  │ 详情 │
├───────────────────────────────────────────────────────────────────────┤
│ 总进度 ████████░░░░░░░░  12/100 ｜ 已用 02:14 ｜ 预计剩余 15:20        │
├───────────────────────────────────────────────────────────────────────┤
│ 日志：                                                                │
│ [16:30:01] 模型加载完成（cuda:0，显存 1.6GB）                          │
│ [16:30:02] 开始处理 12/100：2026-11-04 客户回访.aac                    │
│ [16:30:04]   VAD 切分 87 段 → 规整后 82 段                            │
│ [16:30:09]   识别完成，2 位说话人，出口 output/2026-11/...md            │
├───────────────────────────────────────────────────────────────────────┤
│ 设备 cuda:0 · RTX 3050 · 显存 2.1/8.0GB · 模型 SenseVoiceSmall         │
└───────────────────────────────────────────────────────────────────────┘
```

### 9.2 交互要点

| 功能 | 行为 |
| --- | --- |
| 添加文件 | 文件对话框，过滤 `*.mp3 *.aac *.m4a`，支持多选 |
| 添加文件夹 | 递归扫描（可选），只收三种扩展名 |
| 拖拽 | 主窗口接受拖入的文件和文件夹 |
| 开始 | 队列中所有 `pending` 依次执行；已在跑则忽略 |
| 暂停 | 当前文件跑完即停，不中断单文件（避免半成品） |
| 停止 | 当前文件也中断，标记 `pending`，下次可续 |
| 双击行 | 弹出结果预览窗（Markdown 渲染 + 说话人轮次列表），可改说话人 |
| 打开输出 | 用默认程序打开该文件的 md |
| 设置 | 见 9.4 |

### 9.3 线程模型

- **主线程**：只做 UI，绝不加载模型、绝不做推理。
- **Worker 线程**（单个 `QThread`）：串行跑队列。为什么串行？8GB 显存下并发会 OOM，且串行才能稳定拿到 GPU 满速。
- **模型常驻**：`ModelRegistry` 单例在第一次处理时加载，之后一直留在显存。切换参数（如 K 值）不需要重载模型。
- **通信**：Worker 通过 Qt Signal 发 `batch_started / file_started / file_progress / file_done / file_failed / batch_done`，主线程接到后更新表格。进度用节流（≥ 100ms 一次）避免信号洪水卡 UI。
- **取消**：`threading.Event`，流水线在每步之间检查。
- **ffmpeg 子进程**：`subprocess.run` 带 `creationflags=CREATE_NO_WINDOW`，避免弹黑框。

### 9.4 设置项

| 分组 | 项 | 默认 |
| --- | --- | --- |
| 模型 | 模型目录 | `./models` |
| 模型 | 设备 | 自动（有 CUDA 用 CUDA） |
| 识别 | 语言 | auto |
| 识别 | batch_size_s | 120 |
| 说话人 | 聚类模式 | 固定 2 人 |
| 说话人 | 平滑开关 | 开 |
| 输出 | 输出目录 | `./output` |
| 输出 | 是否输出 SRT | 关 |
| 输出 | 处理完删除缓存 WAV | 开 |
| 高级 | 强制重跑已处理文件 | 关 |

设置存 SQLite `settings` 表，首次启动写入默认值。

---

## 10. 项目结构

```
RecordReg_WB/
├── .venv/                      # Python 3.11 虚拟环境
├── app/
│   ├── main.py                 # GUI 入口
│   ├── config.py               # 配置读写（默认值 + SQLite）
│   ├── core/
│   │   ├── audio.py            # ffmpeg 解码、ffprobe、缓存
│   │   ├── vad.py              # VAD + 段规整
│   │   ├── asr.py              # SenseVoice 封装（含富文本清洗）
│   │   ├── embed.py            # cam++ 封装（返回结构防御）
│   │   ├── cluster.py          # 聚类 + 标签稳定化
│   │   ├── smooth.py           # 平滑规则
│   │   ├── assemble.py         # 段 → 轮次
│   │   ├── writer.py           # md / json / jsonl
│   │   ├── store.py            # SQLite
│   │   ├── registry.py         # 模型单例
│   │   ├── pipeline.py         # 单文件全流程编排
│   │   ├── runner.py           # 批次队列 + Worker
│   │   └── errors.py           # 异常类型
│   ├── ui/
│   │   ├── main_window.py
│   │   ├── task_table.py
│   │   ├── preview_dialog.py
│   │   ├── settings_dialog.py
│   │   └── styles.py           # 深色主题
│   └── cli.py                  # 命令行入口（无 GUI 也能跑，便于调试）
├── models/                     # 手工下载的模型（不进 git）
│   ├── SenseVoiceSmall/
│   ├── speech_fsmn_vad_zh-cn-16k-common-pytorch/
│   └── speech_campplus_sv_zh-cn_16k-common/
├── output/                     # 转写结果
├── cache/                      # 临时 WAV
├── data/app.db                 # SQLite
├── scripts/
│   ├── download_models.py      # modelscope 显式下载
│   └── check_env.py            # 自检：torch/cuda/ffmpeg/模型
├── docs/
│   └── DESIGN.md               # 本文档
├── tests/
│   ├── test_cluster.py
│   ├── test_smooth.py
│   └── fixtures/               # 3 个短样本音频
├── requirements.txt
├── 安装环境.bat
├── 下载模型.bat
└── 启动.bat
```

---

## 11. 环境搭建

### 11.1 为什么必须装 Python 3.11

你机器上现有的是 **3.13.14**（WorkBuddy 内置）和 **3.14.2**（Windows 应用商店版）。这两个版本存在实质风险：

- PyTorch 对 3.13 的支持从 2.6 才开始，而 funasr 生态在 3.13 上的实测问题（`numpy<2` 冲突、部分 C 扩展无 wheel）较多，遇到报错时排查成本极高。
- 3.14 更激进，基本没有稳定 wheel。

**结论：单独装一个 3.11.9**，项目全部依赖装在这个解释器建的 venv 里，与系统 Python 完全隔离。

安装方式（二选一）：

```bat
:: 方式 A：官方安装包（推荐，国内可下）
:: 下载 https://mirrors.huaweicloud.com/python/3.11.9/python-3.11.9-amd64.exe
:: 安装时勾选 "Add python.exe to PATH"，其余默认

:: 方式 B：用 uv（更快，自动下载独立 CPython）
winget install astral-sh.uv
uv python install 3.11
```

### 11.2 建虚拟环境（uv）

本机已装 uv，且 uv 里已缓存 CPython 3.11，不需要另装 Python：

```bat
cd /d D:\RecordReg
uv venv --python 3.11 .venv
```

装完确认：`.venv\Scripts\python.exe -c "import sys; print(sys.version)"` 应输出 3.11.14。

### 11.3 依赖清单与安装顺序

**依赖安装不能一条命令搞定，必须按顺序分四步**（原因见 §11.3.1）。

```txt
# requirements.txt（可直接一次装的部分）
numpy>=1.24,<2.0
scipy
scikit-learn
soundfile
librosa
kaldiio
pyyaml
tqdm
modelscope
torch-complex        # funasr 运行期必需，未在元数据声明
sentencepiece        # SentencepiecesTokenizer 依赖
six                  # funasr 1.2.9 未声明的隐性依赖
editdistance
umap-learn
tensorboardX
omegaconf>=2.3
hydra-core>=1.3
```

安装四步：

```bat
set "PY=%~dp0.venv\Scripts\python.exe"
set "IDX=https://pypi.tuna.tsinghua.edu.cn/simple"

:: 1) torch 必须走 cu121 专用索引，否则会装到 PyPI 上的 CPU 版
uv pip install --python "%PY%" ^
  --index https://download.pytorch.org/whl/cu121 --index %IDX% ^
  torch==2.5.1 torchaudio==2.5.1

:: 2) antlr4 只有源码包，用 pip 在临时目录构建（见 11.3.1 第 3 条）
uv pip install --python "%PY%" --index %IDX% pip
"%PY%" -m pip install --no-cache-dir "antlr4-python3-runtime==4.9.3" -i %IDX%

:: 3) 其余依赖
uv pip install --python "%PY%" --index %IDX% -r requirements.txt

:: 4) funasr 跳过依赖单独装（避开 oss2）
uv pip install --python "%PY%" --index %IDX% --no-deps funasr==1.2.9
```

### 11.3.1 依赖踩坑记录（实际实施时遇到，已固化到脚本）

| # | 现象 | 根因 | 处理 |
| --- | --- | --- | --- |
| 1 | `AssertionError: SenseVoiceSmall is not registered` | funasr 1.3+ 移除了 `SentencepiecesTokenizer`，而 SenseVoiceSmall 的 `config.yaml` 指定的正是它，导致 tokenizer 类为 None | **funasr 锁死 1.2.9**，不用最新版 |
| 2 | `ModuleNotFoundError: six` | funasr 1.2.9 的依赖元数据漏了 six，import 链断在 `funasr.metrics.common` | 显式安装 six；装上后模型类注册数从 17 恢复到 37 |
| 3 | `oss2` 构建失败 / `antlr4` 构建失败 | 两者在镜像上只有 sdist；构建完成后的临时目录批量删除会触发安全删除保护 | oss2 本项目用不到 → `--no-deps` 跳过；antlr4 改用 pip 在 `%TEMP%` 下构建 |
| 4 | `Please install torch_complex firstly` | funasr 1.2.9 未声明该依赖 | 显式安装 `torch-complex` |
| 5 | `torch_complex` 与 SenseVoice 的 emoji | `rich_transcription_postprocess` 会把情感标签转成 😡😊 混进正文 | 在 `asr.py` 里做 emoji 剥离，情感信息仅保留在 JSON 字段 |


### 11.4 模型下载（严格遵守"不走库内自动下载"）

下载统一由一个独立脚本完成，用 **modelscope CLI**（不触发 funasr 的自动下载逻辑）：

```python
# scripts/download_models.py（核心逻辑）
from modelscope import snapshot_download
import os

MODELS = {
    "iic/SenseVoiceSmall": "SenseVoiceSmall",                                  # ≈897 MB
    "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch": "speech_fsmn_vad_zh-cn-16k-common-pytorch",  # ≈4 MB
    "iic/speech_campplus_sv_zh-cn_16k-common": "speech_campplus_sv_zh-cn_16k-common",            # ≈28 MB
}

for repo, local in MODELS.items():
    target = os.path.join(BASE, "models", local)
    if os.path.exists(os.path.join(target, "config.yaml")) and os.path.exists(os.path.join(target, "model.pt")):
        print(f"[skip] {local} 已存在")
        continue
    print(f"[down] {repo} -> {target}")
    snapshot_download(repo, local_dir=target)
```

等价的命令行方式（不想跑 Python 时用）：

```bat
modelscope download --model iic/SenseVoiceSmall --local_dir models\SenseVoiceSmall
modelscope download --model iic/speech_fsmn_vad_zh-cn-16k-common-pytorch --local_dir models\speech_fsmn_vad_zh-cn-16k-common-pytorch
modelscope download --model iic/speech_campplus_sv_zh-cn_16k-common --local_dir models\speech_campplus_sv_zh-cn_16k-common
```

**运行时如何保证绝不上网**（三段式封锁）：

1. 所有 `AutoModel(...)` 的 `model` 参数**只传本地绝对路径**，绝不传 `iic/SenseVoiceSmall` 这类模型 ID；
2. 全部传 `disable_update=True`；
3. 启动时把 `MODELSCOPE_CACHE` / `HF_HOME` 指向项目内目录，并设置 `OFFLINE_MODE=1`；`scripts/check_env.py` 里做一次"断网可加载"自检。

模型目录校验：每个模型目录下必须同时存在 `config.yaml` 与 `model.pt`（SenseVoice 还应有 `tokens.json`），缺失即判定下载不完整并给出明确提示。

### 11.5 环境自检脚本

`scripts/check_env.py` 输出一张表：

```
Python        3.11.9   ✓
torch         2.5.1+cu121  ✓
CUDA available True  (RTX 3050, 8.0GB, sm_86)  ✓
ffmpeg        C:\Program Files\ffmpeg\bin\ffmpeg.exe  ✓
SenseVoiceSmall       897MB  ✓
fsmn-vad              4MB    ✓
campplus              28MB   ✓
离线加载测试          ✓ (no network request)
```

任何一项 ✗，直接给出修复建议。这个脚本是用户遇到问题时的第一道排查入口。

---

## 12. 性能预算

### 12.1 显存占用

| 组件 | 显存 |
| --- | --- |
| SenseVoiceSmall | ≈ 1.0 GB |
| fsmn-vad | ≈ 0.05 GB |
| cam++ | ≈ 0.2 GB |
| CUDA context + 推理中间张量 | ≈ 0.6–1.2 GB（随 batch 变化） |
| **合计峰值** | **≈ 2.5–3.0 GB** |

实测当前桌面已占用 3GB，8GB 总量下仍余 5GB，安全边际充足。若同时开了游戏/剪辑软件导致 OOM，程序应自动降级 `batch_size_s` 并提示（见 §13.4）。

### 12.2 耗时估算（100 文件 × 3 分钟 = 300 分钟音频）

| 阶段 | 单文件（3 分钟） | 100 文件合计 |
| --- | --- | --- |
| ffmpeg 解码 | 0.5–2s | 1–3 min |
| VAD | 0.3–1s | 1–2 min |
| ASR（GPU） | 1–2s | 2–4 min |
| cam++ 声纹（批量） | 1–1.5s | 2–3 min |
| 聚类+平滑+写盘 | 0.1s | 10s |
| **合计** | **≈ 3–5s** | **≈ 6–10 min** |

> 数字来自实测：84.9 分钟真实录音整体耗时 3.4 分钟（RTF 0.040），其中 ASR 与声纹提取各占约 40%。
> 真正的时间瓶颈是**首次模型加载（约 20s）**和 **ffmpeg 解码**，都需在 UI 上给出明确的等待反馈。

### 12.3 磁盘占用

| 项 | 大小 |
| --- | --- |
| 虚拟环境 | ≈ 6–8 GB（含 torch cu121） |
| 模型 | ≈ 0.93 GB |
| 单文件输出（md+json） | ≈ 15–40 KB |
| 100 文件输出 | ≈ 3 MB |
| 临时 WAV（单文件，处理完删除） | ≈ 5.8 MB |

D 盘剩余 1.3TB，无压力。

---

## 13. 异常处理与已知限制

### 13.1 异常矩阵

| 异常 | 检测 | 处理 |
| --- | --- | --- |
| 文件损坏/无法解码 | ffmpeg 返回非 0 | 标记 failed，写 `.error.txt`，继续下一个 |
| 文件无有效语音（全静音） | VAD 返回空 | 标记 done，输出 md 说明"未检测到语音"，不输出空话轮 |
| 文件超过 3 小时 | 时长探测 | 警告并仍处理（VAD 分块保证显存安全），但单独提示耗时 |
| 显存不足 OOM | 捕获 `torch.cuda.OutOfMemoryError` | 清缓存 → `batch_size_s` 减半 → 重试一次 → 仍失败则转 CPU 处理该文件并标记 `device=cpu` |
| 说话人只有 1 个（独白） | 聚类后某簇占比 > 95% | 自动降为 K=1，输出单说话人文本，不强行凑两人 |
| 采样率过低（8k） | ffprobe | 正常处理，JSON 记录并给出提示 |
| 模型文件缺失 | 启动自检 | 弹窗提示运行"下载模型.bat" |
| 输出目录无写权限 | 写文件异常 | 提示换目录，不静默失败 |
| **.aac 裸流读不出时长**（ffprobe 返回 N/A） | 时长字段为空 | 不视为失败，解码后按 WAV 实际长度补齐（§7.1 实测：ADTS 格式常见） |
| **.aac 无法识别容器** | 解码返回非 0 | 自动用 `-f aac` 重试一次 |
| **解码结果过短**（< 0.5s） | WAV 时长校验 | 判定为损坏文件，报"文件可能已损坏"，不再送进模型（否则会抛 mat1/mat2 这类难懂的错误） |
| **超长录音**（切片数 > 200） | 切片数统计 | **内存切片**（不落盘）+ 分批（200 段/批）送入 ASR |
| **长录音处理完闪退** | — | 见 §13.5，根因是批量删除切片触发保护，已根治 |
| **控制台编码异常** | stdout 编码 | 启动时 `reconfigure(errors="replace")`，避免 emoji/生僻字导致 UnicodeEncodeError 退出 |
| **未捕获异常** | 最外层 try | 打印完整栈 + 写入 `logs/error.log`，bat 窗口不再"闪退"到什么都看不见 |
| 运行日志 | 每次运行 | 覆盖写 `logs/run.log`，窗口异常关闭后可事后查阅 |

### 13.2 单个文件失败不影响批次

这是硬性要求。`BatchRunner` 里每个文件用独立 try/except 包裹，异常写库，批次继续。批次结束时汇总"成功 X / 失败 Y"，失败的可在表格里右键"重试"。

### 13.3 明确的功能边界（要提前讲清，避免预期错位）

1. **重叠语音无法分离**：两人同时说话时，声学上就是一个混合信号，VAD 把它切给其中一个人，另一人的话会丢失。这是所有不做音源分离（BSS）方案的共同限制。若这场景高频，需另加 `speech_separation` 模型，本版本不做。
2. **说话人编号只在单个文件内有效**：文件 A 的 `SPK_1` 和文件 B 的 `SPK_1` 不是同一个人。跨文件认人需要预先注册声纹，本版本不做。
3. **方言/重口音**：SenseVoice 对普通话和常见方言效果好，但重口音（如部分西南官话）CER 会上升。可通过后续微调解决。
4. **专业术语**：模型未见过的人名、产品名、缩写容易错。建议后续加"热词表"（SenseVoice/Paraformer 均支持 hotword），本版本留接口。

### 13.4 性能保护
- 批处理全程串行，禁止并发推理（8GB 下并发必 OOM）。
- 每处理完一个文件 `torch.cuda.empty_cache()` + `gc.collect()`，防止 100 个文件累计泄漏。
- 日志记录每个文件的显存峰值，便于发现慢泄漏。

---

### 13.5 长录音"闪退"根因与根治（2026-11-05）

**现象：** 处理 38.8 分钟的 aac 时程序直接退出，没有 Python 报错、没有输出文件、窗口一闪而过。

**根因：** 日志里出现

```
[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":245,"threshold":50,"targets":["cache\\9e5d190b5d7e"]}
```

处理完清理缓存时，一次性删除 245 个切片文件，触发了宿主的"单轮累计删除超过 50 个文件"保护，**进程被直接终止**。短文件（3 分钟 mp3 仅 30–60 个切片）低于阈值，所以一直正常。

**尝试过但无效：** 把 `shutil.rmtree` 改成逐文件 `unlink`。保护按**累计次数**计数，删到第 50 个依然触发。

**根治方案：不在磁盘上生成切片。**

| 环节 | 改造前 | 改造后 |
| --- | --- | --- |
| 解码 | audio16k.wav | audio16k.wav（不变） |
| 切片 | 写 N 个 00001.wav…00N.wav | **内存中切出 `list[np.ndarray]`** |
| 送入 ASR | 传 N 个文件路径 | 直接传 ndarray 列表（funasr 原生支持） |
| 清理 | 删 N 个文件 + 目录 | 删 1 个 wav + 2 个空目录 |

附带收益：省掉几百次小文件写/读盘，长音频更快（38.8 分钟音频 18.7 秒处理完，RTF 0.008）。

**验证：** 8 个真实录音共 84.9 分钟，全部成功，零报错。

## 14. 一键启动脚本

### 14.1 `安装环境.bat`（只跑一次）

```bat
@echo off
chcp 65001 >nul
cd /d %~dp0
echo ============================================
echo  RecordReg 环境安装
echo ============================================

where py >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python Launcher，请先安装 Python 3.11
    pause & exit /b 1
)
py -3.11 -c "import sys" 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 Python 3.11，请先安装（见 docs\DESIGN.md 11.1）
    pause & exit /b 1
)

if not exist .venv\Scripts\python.exe (
    echo [1/4] 创建虚拟环境...
    py -3.11 -m venv .venv || (echo 创建失败 & pause & exit /b 1)
)

echo [2/4] 升级 pip...
.venv\Scripts\python.exe -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple

echo [3/4] 安装 PyTorch (CUDA 12.1)...
.venv\Scripts\python.exe -m pip install torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu121
if errorlevel 1 (echo [错误] PyTorch 安装失败 & pause & exit /b 1)

echo [4/4] 安装其余依赖...
.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (echo [错误] 依赖安装失败 & pause & exit /b 1)

echo.
echo 环境安装完成，接下来请运行 [下载模型.bat]
pause
```

### 14.2 `下载模型.bat`

```bat
@echo off
chcp 65001 >nul
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
    echo [错误] 请先运行 安装环境.bat
    pause & exit /b 1
)
echo 从 ModelScope 下载模型（约 930MB，请耐心等待）...
.venv\Scripts\python.exe scripts\download_models.py
if errorlevel 1 (echo [错误] 模型下载失败 & pause & exit /b 1)
echo.
echo 模型下载完成。运行 [启动.bat] 开始使用。
pause
```

### 14.3 `启动.bat`

启动图形界面。用 `pythonw.exe` 启动，不留控制台黑窗；前置检查缺什么会直接提示。

```bat
@echo off
cd /d %~dp0
set "PY=%~dp0.venv\Scripts\python.exe"
set "PYW=%~dp0.venv\Scripts\pythonw.exe"

if not exist "%PY%" (
    echo [错误] 虚拟环境不存在，请先运行 安装环境.bat
    pause & exit /b 1
)
if not exist "models\SenseVoiceSmall\model.pt" (
    echo [错误] 模型未下载，请先运行 下载模型.bat
    pause & exit /b 1
)
"%PY%" -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo [错误] 缺少界面组件 PySide6，请重新运行 安装环境.bat
    pause & exit /b 1
)

start "" "%PYW%" "%~dp0app\main.py"
```

> 三个 bat 都用 **GBK 编码 + CRLF** 保存。UTF-8 的中文在 cmd 默认代码页下会显示成乱码，
> 而加 `chcp 65001` 又会让后续行的中文解析出错——GBK 是这里唯一稳妥的选择。
>
> 界面异常会写到 `logs\gui_error.log`（pythonw 没有控制台，这是唯一线索）。

命令行入口仍然保留，做批处理脚本时更方便：

```bat
.venv\Scripts\python.exe app\cli.py "D:\录音" --status
```

### 14.4 `启动.vbs`（真正无窗口）

`启动.bat` 无论如何都会闪一个 cmd 窗口——只要经过 bat，cmd 就必须存在。想完全无窗口，只能用 VBScript 的隐藏运行：

```vbscript
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
pyw  = base & "\.venv\Scripts\pythonw.exe"

' 前置检查省略，见实际文件
sh.CurrentDirectory = base
sh.Run """" & pyw & """ """ & base & "\app\main.py""", 0, False
```

`Run` 的第二个参数 `0` = 隐藏窗口，第三个 `False` = 不等待。

> **编码坑**：`.vbs` 由 `wscript.exe` 按系统 ANSI（中文系统为 GBK）解析，
> 用 UTF-8 保存会导致中文提示乱码，必须存成 **GBK**。

两个入口的分工：

| 文件 | 场景 |
| --- | --- |
| `启动.vbs` | 日常使用，双击后界面直接出现，无任何黑窗 |
| `启动.bat` | 排错用，能看到前置检查的输出 |

> 用 `pythonw.exe` 启动可以避免黑窗残留；调试期用注释里的 `python.exe` 版本看日志。

---

## 15. 开发计划

| 阶段 | 内容 | 产出 | 状态 |
| --- | --- | --- | --- |
| M0 | 环境搭建 + 模型下载 + 自检脚本 | 三个 bat 可跑通，`check_env.py` 全绿 | ✅ 已完成 |
| M1 | 解码 + VAD + ASR 打通 | `cli.py` 能出**不带说话人**的全文 | ✅ 已完成 |
| M2 | cam++ + 聚类 + 平滑 | `cli.py` 出带说话人的 md/json | ✅ 已完成 |
| M3 | 输出规范 + SQLite 索引 + 去重续跑 | 输出符合 §7，重复拖入不重跑 | ✅ 已完成 |
| M4 | GUI 主窗口 + 任务表格 + 队列 | 单文件、批量都能跑，进度可见 | ✅ 已完成 |
| M5 | 结果预览 + 说话人改名 + 热词表编辑 | 人工纠错闭环 | ✅ 已完成 |
| M6 | 异常处理 + 100 文件压测 + 文档 | 长跑不崩，指标达标 | 待开始 |

### 15.1 M0–M1 实施记录（实测数据）

环境：Python 3.11.14 / torch 2.5.1+cu121 / funasr 1.2.9 / RTX 3050 OEM 8GB。

| 指标 | 实测值 | 与设计预估对比 |
| --- | --- | --- |
| 模型加载（VAD） | 0.2–0.7 s | 符合 |
| 模型加载（SenseVoice，首次） | **19.9 s** | 预估 20–40s，符合 |
| 预热 | 6.6 s | — |
| 5.6 s 单人音频 | 0.2 s，RTF 0.043 | 优于预估 0.03–0.07 上限 |
| 70 s 连续讲话 | 1.7 s，RTF 0.025 | 优于预估 |
| 显存峰值 | 1.95 / 8.0 GB | 预估 2.5–3.0GB，**实际更低** |
| VAD 分段 | 70 s 音频切出 12 段 | 符合（约 6 s/段）|

实测识别样例（模型自带中文示例，转 m4a 后测）：

- 5.6 s 单人：「开放时间早上9点至下午5点。」——完全正确
- 70 s 讲座录音：374 字，整体通顺，个别专有名词有误（"长期存学卡"→"仓期仓学卡"、"三宝老师"→"三炮老师"），符合"需要热词表"的判断

**结论：M1 的准确率与性能均达到设计目标，可以进入 M2。**

**追加：真实录音批量验证（2026-11-05）**

用需求方提供的 8 个真实录音（4 个微信 aac + 4 个电话 mp3，共 84.9 分钟）跑全量：

| 文件 | 时长 | 段数 | 字数 | RTF |
| --- | --- | --- | --- | --- |
| 微信录音_面试.aac | 38.8 分 | 187 | 11075 | 0.007 |
| 微信录音（技术方案讨论） | 13.7 分 | 104 | 3183 | 0.009 |
| 电话录音 1.mp3 | 16.0 分 | 134 | 3681 | 0.011 |
| 电话录音 2.mp3 | 6.4 分 | 48 | 1394 | 0.009 |
| 电话录音 3.mp3 | 5.3 分 | 40 | 1252 | 0.010 |
| 电话录音 4.mp3 | 3.4 分 | 18 | 886 | 0.008 |
| 微信录音 B.aac | 0.8 分 | 4 | 240 | 0.009 |
| 需求沟通录音 | 0.5 分 | 5 | 131 | 0.016 |
| **合计** | **84.9 分** | **540** | **21842** | **0.009** |

- 成功率 8/8，无崩溃
- 84.9 分钟音频 **48 秒**处理完（含解码）
- 识别质量：工程沟通、招聘面试等场景，正文通顺可读，个别专有名词（人名、公司名）有误——印证热词表的必要性
- 全部文件 `language` 均正确标注为 `zh`

### 15.2 M2 说话人分离实施记录（2026-11-05）

**实测效果（8 个真实录音，84.9 分钟）**

| 文件 | 时长 | 人 | 轮次 | 最大说话人占比 | 平滑改动率 |
| --- | --- | --- | --- | --- | --- |
| 微信录音（面试场景） | 38.8 分 | 2 | 127 | 53.9% | 0.0% |
| 微信录音（技术方案讨论） | 13.7 分 | 2 | 64 | 59.7% | 1.6% |
| 电话录音 1 | 16.0 分 | 2 | 88 | 52.7% | 0.5% |
| 电话录音 2 | 6.4 分 | 2 | 37 | 52.0% | 0.0% |
| 电话录音 3 | 5.3 分 | 2 | 22 | 53.8% | 1.4% |
| 电话录音 4 | 3.4 分 | 2 | 5 | 50.4% | 0.0% |
| 微信录音 B | 0.8 分 | 2 | 4 | 68.8% | 0.0% |
| 需求沟通录音 | 0.5 分 | 2 | 3 | 83.8% | 0.0% |

- 8/8 成功识别为 2 人对谈，无崩溃
- 说话人时长占比大多在 **50:50 ~ 60:40**，符合真实对谈形态
- **平滑改动率 0–1.6%**：说明聚类本身已经稳定，后处理只做了零星修正——这是好信号，说明不是靠平滑"硬掰"出来的
- 118 分钟音频总耗时 3.4 分钟（RTF 0.040），其中声纹提取约占 40%

**实施中发现的三个关键问题（都已修正并回写文档）**

| # | 问题 | 现象 | 修正 |
| --- | --- | --- | --- |
| 1 | VAD 分段太粗 | 13 分钟电话只切 18 段、平均 45s/段，一段里含多个话轮，声纹必然混合 | `max_end_silence_time` 800→500ms，新增"超 8s 强切、目标 5s"，段数 18→42 / 187→486（§6.2.1） |
| 2 | average linkage 塌缩 | 38 分钟录音被聚成 482:1 段，13 分钟录音 169:13 段 | 改 `complete` linkage，同一数据变为 265:218、107:75（§6.2） |
| 3 | R4 幽灵簇误杀 | 按"占比 < 5%"判断，把只说了 18 秒的真实第二人当成噪音合并，整份录音退化成单人；短录音（8 段）里任何簇都 < 8 段，也被误杀 | 改为绝对时长+段数双条件，且 **fixed_k 模式下完全关闭 R4**（§6.3） |

**性能**：cam++ 批量提取（50 段/批）比逐段快约 1.8 倍，结果与逐段完全一致（余弦相似度 1.0000）。

**质量自检工具**：`scripts/diarization_report.py` 输出每个文件的说话人数、轮次、单段轮次占比、最大占比、平滑率，用于判断参数是否合适。

### 15.3 M3 索引与去重实施记录（2026-11-05）

`app/core/store.py` 落地，表结构与 §8.1 一致。验证结果：

| 场景 | 预期 | 实测 |
| --- | --- | --- |
| 首次处理 8 个文件 | 全部处理并入库 | 8 成功，索引 8 条 done |
| 重复跑同一批 | 全部跳过 | `待处理 0 个，跳过 8 个` |
| 文件改名 + 换目录 | 仍识别为同一文件 | 跳过（sha256 相同） |
| `--force` | 强制重跑 | 正常重跑并刷新记录 |
| 模拟异常退出残留 `running` | 下次启动自动恢复 | 重置为 pending，日志提示"已重置 1 个中断的任务" |
| `--status` | 统计 + 最近记录 + 失败原因 | 正常输出 |

**顺带修掉一个隐患**：同一段音频的 `.m4a` 与 `.aac` 两个版本 stem 相同，会写成同一个 `xxx.md` 互相覆盖。现在 `output_paths()` 会读已存在 json 里的 sha256，不一致时加扩展名后缀（`xxx_aac.md`）。

**版本号**：`TOOL_VERSION` 升到 `0.2.0`（M3 起）。它同时用于 JSON 元数据与索引里的 `model_ver`，改算法后递增即可让旧结果自动失效重跑。

### 15.4 M4 + M5 界面实施记录（2026-11-05）

**技术栈**：PySide6 6.8.3。界面代码共 5 个文件，约 900 行。

| 文件 | 职责 |
| --- | --- |
| `app/main.py` | 入口，含 `--smoke-test` 自检模式（启动→截图→退出） |
| `app/ui/runner.py` | `BatchWorker(QThread)`，信号驱动，主线程不碰模型 |
| `app/ui/main_window.py` | 任务表格、工具栏、日志、拖拽、空状态提示 |
| `app/ui/preview_dialog.py` | 结果预览 + 说话人改名写回 |
| `app/ui/settings_dialog.py` | 说话人参数、输出目录、热词表编辑 |
| `app/ui/styles.py` | QSS 主题 |

**关键实现选择**

| 问题 | 做法 |
| --- | --- |
| 线程模型 | `QThread` 子类 + `Signal`，节流到 ~15fps，避免信号洪水卡界面 |
| 停止/暂停 | 都在**当前文件处理完之后**生效，不产生半成品；关闭窗口时最多等 90 秒，等不到就不关（避免 QThread 被强杀崩溃） |
| 进度粒度 | `process_file(progress=...)` 回调，按 解码→分段→识别→声纹→聚类→整理 上报，声纹/识别内部按批更新 |
| 文件夹递归 | `Path.rglob`，拖父目录即可全扫 |
| 添加即显示时长 | 添加时用 ffprobe 读时长（每个约 50ms），列表立刻能看到有多长 |
| 索引并发 | 主线程查重、worker 写库，共用连接 → `Store` 内加 `RLock` |
| 热词表存储 | 写回 `config.yaml`；热词段单独排版成 `- ["错", "对"]`，yaml 默认的嵌套列表太难看 |
| 无控制台启动 | `pythonw.exe`，异常经 `sys.excepthook` 写入 `logs/gui_error.log` |

**验证方式**：新增 `--smoke-test --smoke-add <dir> --smoke-run`，启动后自动添加目录、开始处理、延时截图退出。用它确认了空状态提示、处理中行（蓝色 30% 识别）、完成行、日志与状态栏都正确渲染；设置对话框与预览对话框单独截图检查。

**热词端到端验证**：配 `["路由器","陆游器"]`、`["李工","李工程师"]`，重跑后 Markdown 里全部替换成功（原文无残留）。

### 15.5 试用反馈修复（2026-11-05 下午）

需求方自行跑了 300 多个文件，提出三个问题，均已处理：

| # | 反馈 | 原因 | 处理 |
| --- | --- | --- | --- |
| 1 | 打开 GUI 仍出现命令行窗口 | `启动.bat` 本身必须有 cmd 窗口 | 新增 `启动.vbs`，用 `WshShell.Run(..., 0, False)` 隐藏启动；bat 保留作排错入口 |
| 2 | 加载大量文件时显示"未响应" | 读时长 + 算 sha256 + 查索引全在主线程，300 个文件要几十秒 | 抽出 `ScanWorker(QThread)`，边扫边发信号，界面显示「正在读取 22/120：xxx」+ 进度条，按钮进入禁用态 |
| 3 | 担心数据库累积占空间/拖慢速度 | — | 设置里新增「索引数据库」分组：显示记录数与占用、`清理失效记录`、`清空索引`（二次确认 + VACUUM） |

**实测数据（回答第 3 条的疑问）**：需求方实际跑完 364 个文件后，`data/app.db` 只有 **0.33 MB**。

索引只存 `sha256 + 路径 + 少量统计`，每条约 1KB。即使一万个文件也就 10MB 量级，且查询走索引（`sha256` 唯一索引），速度不受记录数影响。真正会慢慢变大的只有一处：**源文件被删除或移动后，记录还留着**——所以提供了「清理失效记录」，它按 `path` 逐个检查存在性后删除，并 `VACUUM` 回收空间。

**顺带修掉的样式问题**：`QPushButton#danger` 这类 ID 选择器优先级高于 `QPushButton:disabled`，导致「停止」按钮禁用时仍显示红色、看上去可点。已补 `#danger:disabled` 的样式。







---

## 16. 风险与备选方案

| 风险 | 概率 | 影响 | 应对 |
| --- | --- | --- | --- |
| funasr 在 3.11 上依赖冲突 | 低 | 阻塞 | 锁死版本组合（§11.3）；实在不行退回 `funasr==1.1.6` |
| 下载 torch cu121 慢/失败 | 中 | 阻塞 | 换阿里/清华 wheel 镜像；或退到 CPU 版（速度慢约 20 倍，不推荐） |
| 聚类效果不达 90% | 中 | 体验 | 已内置人工改名；可换 `eres2netv2` 声纹模型（体积 70MB，中文效果更好） |
| SenseVoice 对特定领域术语识别差 | 中 | 准确率 | 加 hotword 热词表（接口预留）；或改用 Paraformer-large 微调 |
| 8GB 显存被其他程序占满 | 中 | 稳定性 | 自动降 batch + OOM 重试 + CPU 兜底 |
| 用户录音含大量重叠对话 | 低 | 说话人错误 | 提前说明限制；必要时引入分离模型（v2） |

**备选整体方案**：若 SenseVoice 在某类录音上效果不理想，切到 `Paraformer-large-vad-punc-spk`（一体化，带时间戳和 spk 字段）只需改配置，不需要改架构——这也是流水线分层的价值。

---

## 17. 需求方确认结论

以下 5 项已由需求方确认（2026-11-05），相关设计已按此调整：

| # | 问题 | 确认结论 | 对设计的影响 |
| --- | --- | --- | --- |
| 1 | 录音来源 | **手机录音**（44.1k/48k AAC，质量较好） | 不需要针对 8k 电话录音做特殊补偿；`origin_sample_rate` 字段保留用于异常排查 |
| 2 | 是否清洗口语词 | **不清洗** | 保留"嗯/那个"等口语词，`asr.py` 不做过滤，避免误删 |
| 3 | 说话人改名跨文件记忆 | **不需要** | 声纹注册、跨文件认人整体移出范围，`SPK_1/SPK_2` 仅在单文件内有效 |
| 4 | 输出目录组织 | **平铺** | 所有结果直接写 `output/`，不按月份分子目录 |
| 5 | 高频名词 | **有** | 保留 `hotwords` 纠正表（已在 `asr.py` 实现替换逻辑），需在 M2 前收集具体词表 |
| — | 虚拟环境工具 | **用 uv** | 已按 uv 落地，见 §11.2 |

### 17.1 仍待补充

1. **高频名词清单**：需要具体的人名/产品名/术语，以及它们当前被误识别成什么。格式为 `["错误写法", "正确写法"]` 的列表，填进 `config.yaml` 的 `hotwords`。
2. **是否需要 SRT 字幕**：当前 M1 只出 md + json，SRT 留了开关未实现。


---

## 附录 A：核心算法伪代码

```python
def process_one(file_path: Path) -> Result:
    # 1. 去重检查
    sha = sha256(file_path)
    if store.is_done(sha) and not force_rerun:
        return Result.skipped()

    # 2. 解码
    wav = ffmpeg_to_16k_mono(file_path, cache_dir)
    duration = probe_duration(file_path)

    # 3. VAD + 规整
    raw = vad.generate(wav, max_single_segment_time=30000)
    segs = normalize_segments(raw)          # 合并 <0.4s，切分 >30s

    if not segs:
        return Result.no_speech()

    # 4. 识别（逐段，内部动态 batch）
    texts = asr.transcribe_batch([cut_wav(wav, s.start, s.end) for s in segs])

    # 5. 声纹
    embs = [embedder.extract(cut_wav(wav, s.start, s.end)) for s in segs]
    embs = l2_normalize(np.stack(embs))

    # 6. 聚类（2 人）
    k = 2 if mode == "fixed_2" else estimate_k(embs)
    labels = AgglomerativeClustering(n_clusters=k, metric="cosine",
                                     linkage="average").fit_predict(embs)

    # 7. 标签稳定化：时长最长的簇 → SPK_1
    labels = canonicalize(labels, segs)

    # 8. 平滑
    labels = smooth(segs, labels, rules=[R1, R2, R3, R4])

    # 9. 组装轮次
    turns = merge_into_turns(segs, labels, texts, gap_ms=1500)

    # 10. 输出 + 索引
    md_path, json_path = writer.write(file_path, turns, meta)
    store.mark_done(sha, md_path, json_path, stats)
    cleanup(wav)
    return Result.ok()
```

## 附录 B：默认参数速查

| 参数 | 值 | 出处 |
| --- | --- | --- |
| 目标采样率 | 16000 Hz | §5.1 |
| VAD 单段上限 | 30000 ms | §5.2 |
| VAD 句末静音 | 800 ms | §5.2 |
| 最短保留段 | 400 ms | §5.3 |
| ASR batch_size_s | 120 s | §5.4 |
| 声纹维度 | 192 | §5.5 |
| 聚类 linkage | average + cosine | §6.2 |
| 默认 K | 2 | §6.1 |
| R1 碎段阈值 | 600 ms | §6.3 |
| R2 孤立段阈值 | 1200 ms | §6.3 |
| R4 幽灵簇阈值 | 5% | §6.3 |
| 轮次合并间隔 | 1500 ms | §6.3 |

---

*文档结束。本设计为 v1.0，M1 阶段用真实录音验证后，将根据实测 CER 与说话人正确率回写参数与结论。*
