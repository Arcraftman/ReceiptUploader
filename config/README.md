# 配置目录

| 文件或目录 | 职责 | 是否提交 |
|---|---|---|
| `kdzwy.json` | 登录账号和密码 | 否 |
| `kdzwy.example.json` | `kdzwy.json` 安全占位示例 | 是 |
| `app.json` | 账无忧域名、超时和 User-Agent | 否 |
| `app.example.json` | `app.json` 示例 | 是 |
| `pipeline.defaults.json` | OCR/LLM 并发与模型技术参数 | 是 |
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

动态账套注册表由公司发现流程写入 `runtime/registry/accountbooks.json`，不属于稳定配置。每月运行参数只写入 `data/inbox/<公司>/<YYYY-MM>/project.json` v5。

本目录不再包含 `datasets.json`、`accountbooks.json`、`month.conf` 或公司级运行开关。
