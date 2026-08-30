# 配置目录

| 文件或目录 | 职责 | 是否提交 |
|---|---|---|
| `kdzwy.json` | 登录账号和密码 | 否 |
| `kdzwy.example.json` | `kdzwy.json` 安全占位示例 | 是 |
| `app.json` | 账无忧域名、超时和 User-Agent | 否 |
| `app.example.json` | `app.json` 示例 | 是 |
| `pipeline.defaults.json` | OCR/LLM 并发与模型技术参数 | 是 |
| `bank_exception.defaults.json` | 新公司、新月份首次创建时复制的通用银行特殊对象默认值 | 是 |
| `template_companies.json` | 模板公司注册表与默认基础模板 | 是 |
| `companies/` | 资料公司身份和跨月份共享模板 | 否 |

公司配置统一命名为 `company_<company_id>_<真实公司名>.json`，格式为 version 3，只允许：

```json
{
  "version": 3,
  "company_key": "company_17867515",
  "company_id": "17867515",
  "company_name": "上海微誉信息技术有限公司",
  "template_company": "weiyu"
}
```

动态账套注册表由公司发现流程写入 `runtime/registry/accountbooks.json`，不属于稳定配置。每月运行参数只写入 `data/inbox/<公司>/<YYYY-MM>/project.json` v7；其中必须同时显式声明 `dataset`、`target` 和四个业务各自的核心运行字段。当月全部银行规则也只写在 `sources.bank.banks`。

`bank_exception.defaults.json` 只在一个月份首次创建、且该月尚无 `sources.bank.exceptions` 时复制一次。目前默认名称数组只包含通用 TIPS 电子缴税对象；`pdf_keywords` 是系统识别无索引 PDF 的技术规则。之后修改全局默认不会覆盖已有月份。用户在对应月份的 `project.json` 中只需向 `sources.bank.exceptions` 数组添加流水表对手方列出现的完整名称。

本目录不再包含 `datasets.json`、`accountbooks.json`、`month.conf` 或公司级运行开关。
