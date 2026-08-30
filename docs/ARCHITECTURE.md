# 项目架构

## 配置模型

```text
资料公司 v3（跨月份身份 + template_company）
             │
             ├── templates/<template_company>/（跨月份共享）
             │
             └── 月份 project.json v7
                   ├── dataset：明确资料来源公司
                   ├── target：明确目标账套
                   ├── input：本月 Excel 文件名与列
                   ├── defaults：四个业务共享的高级默认值
                   └── sources：四个业务各自精确的 enabled/mode/stage/preload 与直接覆盖
                         └── bank.banks：当月多银行科目、裁剪与流水列的唯一配置源

公司发现 ──> runtime/registry/accountbooks.json v2（自动生成）
技术默认 ──> config/pipeline.defaults.json v2
```

运行配置合并顺序只有：技术默认 → 月份 `defaults` → source 直接覆盖 → 命令行维护性临时覆盖。

## 稳定目录

```text
commands/                         Windows 用户入口
config/                           稳定配置和本地私密配置
data/inbox/<资料公司>/<月份>/     原始资料与 project.json
templates/<模板公司>/             跨月份共享模板与提示词
runtime/registry/                 自动发现的账套注册表
http_sessions/                    登录与公司会话
workspaces/<账号>/<目标账套>/     生成物、状态和日志
scripts/commands/                 Python 命令编排
scripts/windows/                  登录、发现与菜单
src/kdzwy_receipt_uploader/       核心应用包
```

同主体工作区为 `workspaces/<login>/<target>/<month>`；跨主体增加 `from_<source_company_key>` 层。

## 运行链路

```text
project.json v7 预检
  -> dataset、目标账套和会话身份校验
  -> sales/purchase XLSX 与 PDF 映射；bank 确定性拆分
  -> OCR
  -> 规则缩小模板候选
  -> Qwen 结构化模板选择
  -> 动态账套科目和辅助对象解析
  -> receipt 生成与预审
  -> dry-run 或显式 confirm
  -> 凭证与附件回读校验
```

## 核心边界

- `project.json.dataset` 的 key、公司 ID、公司名必须同时匹配资料公司配置。
- `project.json.target` 的 key、公司 ID、公司名必须同时匹配运行期账套注册表。
- 每月 source 独立，四个业务都必须精确声明 `enabled/mode/analysis_stage/preload_items`；新月份默认全部关闭，不继承其他月份。
- bank 不再使用第二份裁剪配置；每家银行的 `bank_account_number`、`split` 和 `statement_columns` 统一放在 `project.json.sources.bank.banks`。
- 每家银行的固定科目号会注入模板候选与提示词，并在模板渲染时覆盖历史模板中的银行存款科目；已有分析和最终 receipt 生成前再次强校验。
- 银行现金流入记录的非金额借方单元格若完全由数字串组成，这些数字会直接替换模板 `explanation_body`；银行存款分录另从 OCR 原文追加交易日期，且已有分析复用与最终 receipt 前都会校验这两项确定性规则。
- bank 模板先按流水方向硬筛选；唯一候选走确定性选择，多个合法候选才调用精简上下文的 Qwen。显式启用 bank `preload_items` 时，辅助核算对象从 bank map 创建到目标账套。
- bank 的 `configCompany` 固定来自 `dataset.company_name`；银行借方有效金额的对手方固定为供应商，贷方有效金额的对手方固定为客户，Excel 配置列是权威值，OCR/LLM 不得覆盖。
- bank 遵循“裁剪 → 特殊对象物理分流 → 剩余 OCR/匹配 → LLM → 人工复核 → prepare+existing 最终 receipt → verify/dry-run → confirm”阶段；特殊对象 PDF 保留裁剪原件并复制到专用目录，同时从普通后续输入中排除；OCR、匹配和 LLM 阶段禁止提前生成 receipt。
- 未匹配流水只写入报告并标记为不可进入下游；`unmatched` 命令只负责列出，不会启动任何业务处理。
- 对手方为保守规则识别出的个人姓名时，在 OCR 调度前按流水索引排除并清除旧 OCR 缓存；只写报告，不进入供应商/客户、匹配、LLM、模板和 receipt。
- 四类资料目录始终存在，但只有 `enabled=true` 的业务实际运行。
- 模板 JSON 同时保存分类规则与会计分录；`index.json` 不重复模板清单。
- 动态科目和辅助对象 ID 只能来自当前目标账套。
- 生成物不写入 `data/inbox`，只写隔离工作区。
- 真实上传串行执行；任一失败或歧义都会停止后续任务。
- 人工银行 receipt 完成后由用户手动从 `draft=true` 改为 `draft=false`；`verify` 和真实提交入口使用同一校验器，任何剩余草稿或无效字段都会阻断整批提交。
- 银行和杂项在业务规则尚未完成时继续保持真实上传阻断。

## 不兼容边界

以下旧配置已移除：

- `config/datasets.json`
- `config/accountbooks.json`
- `month.conf`
- `project.json` v6 及更早版本
- 公司 JSON 中的 `dataset`、`enabled` 和运行字段
- accountbook/dataset 级 `pipeline_overrides`
- source 中的嵌套 `overrides`
- 模板 `classification_rules.json` 与 index 重复清单

遇到旧字段时程序直接报错，不做猜测或自动兼容。
