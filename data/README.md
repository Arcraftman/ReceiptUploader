# 原始资料目录

`data` 只保存用户提供的原始资料和月份自描述配置，不保存 OCR、映射、receipt 或日志。

标准结构：

```text
data/inbox/company_<company_id>_<真实公司名>/<YYYY-MM>/
├─ project.json                 # 本月 mode、analysis_stage、sources 等唯一运行配置
├─ month.conf
└─ input/
   ├─ sales/
   ├─ purchase/
   ├─ bank/
   └─ misc/
```

必须先运行 `commands\initialize_month.bat` 创建结构。每个新月份生成独立 `project.json`，不会继承其他月份的运行开关；跨月份共享的 `template_company` 只配置在 `config/companies`。项目不扫描或兼容其他旧目录布局。
