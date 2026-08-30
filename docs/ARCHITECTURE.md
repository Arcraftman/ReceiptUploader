# 项目架构

## 配置模型

```text
资料公司 v3（跨月份身份 + template_company）
             │
             ├── templates/<template_company>/（跨月份共享）
             │
             └── 月份 project.json v5
                   ├── target：明确目标账套
                   ├── input：本月 Excel 文件名与列
                   ├── defaults：本月运行默认值
                   └── sources：sales/purchase/bank/misc 执行开关与直接覆盖

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
project.json v5 预检
  -> 目标账套和会话身份校验
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

- `company_key` 表示资料公司，不隐式表示目标账套。
- `project.json.target` 的 key、公司 ID、公司名必须同时匹配运行期账套注册表。
- 每月 source 开关独立，新月份默认全部关闭，不继承其他月份。
- 四类资料目录始终存在，但只有 `enabled=true` 的业务实际运行。
- 模板 JSON 同时保存分类规则与会计分录；`index.json` 不重复模板清单。
- 动态科目和辅助对象 ID 只能来自当前目标账套。
- 生成物不写入 `data/inbox`，只写隔离工作区。
- 真实上传串行执行；任一失败或歧义都会停止后续任务。
- 银行和杂项在业务规则尚未完成时继续保持上传阻断。

## 不兼容边界

以下旧配置已移除：

- `config/datasets.json`
- `config/accountbooks.json`
- `month.conf`
- `project.json` v4 及更早版本
- 公司 JSON 中的 `dataset`、`enabled` 和运行字段
- accountbook/dataset 级 `pipeline_overrides`
- source 中的嵌套 `overrides`
- 模板 `classification_rules.json` 与 index 重复清单

遇到旧字段时程序直接报错，不做猜测或自动兼容。
