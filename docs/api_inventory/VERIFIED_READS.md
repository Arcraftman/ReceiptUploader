# 账无忧只读接口实测清单

扫描日期：2026-09-18。域名：`https://vip4-kj.kdzwy.com`。

静态提取 1130 个请求方式与 URL 模板组合；GET 读取候选 372 个，POST 查询候选（待逐项确认）45 个。实测业务成功的读取接口 60 个（按方法、路径、m 动作去重）。

候选与实测口径不同，不能直接相加。GET 并不保证只读；例如 `GET /jdy-fi/{dbId}/gl/v1/itemClass/delete` 已排除。

参数表仅列实际观察到的字段，不表示字段均必填。完整请求示例、返回字段和成功状态见 [verified_reads.json](verified_reads.json)；源码证据见 [all_interfaces.json](all_interfaces.json)。

| 方法 | 路径 / 动作 | 实测参数字段 | 返回数据字段 / 类型 |
|---|---|---|---|
| GET | `/basedata/initParams?m=getSystemParams` | m | resourcesRUL, CURPERIOD, DBID, queryTypeId, companyCoid, isFree, vchCreateAuditAllowSameOne, curDate, geetestCaptchaId, isMobile, weekDate, companyServiceType, hasOriginalVoucher, paymentAccountNumber, siRecycleDateRemainingDay, siId |
| GET | `/basedata/initParams?m=getCommonFunctions` | m | fiSwitch, balReportExchange, cashflowSimpleExchange, wizard, cashflowStdExchange, profitReportExchange, order |
| GET | `/bs/assist.do?m=list` | m, type | totalsize, items |
| GET | `/bs/currency?m=findAll` | m | totalsize, items |
| GET | `/gl/cashier/cashTypeAction?m=list1` | m | dict |
| GET | `/gl/cashier/cashierAccountAction?m=list` | m | totalsize, items |
| GET | `/gl/generatecode?m=findAll` | m | totalsize, items |
| GET | `/gl/voucher?m=findUsedVchNO` | m | totalsize, items |
| GET | `/jdy-fi-bd/{dbId}/v1/account/` | expand | rows |
| POST | `/jdy-fi-rpt/{dbId}/v1/balance-item-report/query` | accountNumber, balance, cur, detail, fromPeriod, happen, includeAccount, itemClassIds, itemNumbers, showQty, toPeriod | list |
| POST | `/jdy-fi-rpt/{dbId}/v1/cost-detail/list` | accountNo, accountType, classIds, periodFrom, periodTo, showFullName, showItem, showProp, showYtdAmount, showZero | list |
| POST | `/jdy-fi-rpt/{dbId}/v1/gl/balance-report` | currency, detailAccount, expandLevels, fromLevel, fromPeriod, fromSubject, isAllSubject, isBalanceZero, isDisabledAccount, isDisplaySubtotal, isHappenAndBalanceZero, isIncludeItem, isMulCur, isSubjectFullName, itemClassId, itemId, toLevel, toPeriod, toSubject | head, item, records |
| POST | `/jdy-fi-rpt/{dbId}/v1/gl/general/query-total` | balance, currency, detailAccount, fromLevel, fromPeriod, fromSubject, happen, includeItem, noHappen, queryString, toLevel, toPeriod, toSubject | item, records |
| GET | `/jdy-fi-rpt/{dbId}/v1/qtyDetailAccount/isNewPurchase` | 无 | bool |
| POST | `/jdy-fi-rpt/{dbId}/v1/qtyTotalAccount/detail` | balance, detail, detailByPeriod, fromAccountId, fromLevel, fromPeriod, happen, includeItem, noHappen, priceFormat, qtyFormat, showNoHappenPeriod, toAccountId, toLevel, toPeriod | rows, size |
| GET | `/jdy-fi-tp/{dbId}/ai/v1/review-status` | yearPeriod | yearPeriod, reviewStatus, showPrompt, outInvoiceReviewCount, inInvoiceReviewCount, bankBillReviewCount |
| GET | `/jdy-fi-tp/{dbId}/follow/kJFactory/isCorpFileBeta` | 无 | bool |
| GET | `/jdy-fi-tp/{dbId}/vat/v1/invoice-stampItems` | 无 | list |
| GET | `/jdy-fi/{dbId}/ai/v1/ai-match/beta` | 无 | journalBeta, invoiceBeta |
| GET | `/jdy-fi/{dbId}/bs/v1/account-class` | 无 | rows |
| GET | `/jdy-fi/{dbId}/bs/v1/currency` | 无 | rows |
| GET | `/jdy-fi/{dbId}/bs/v1/dept` | 无 | depts, totalsize |
| GET | `/jdy-fi/{dbId}/bs/v1/employee` | page, pageSize | rows, records, totalPage, page |
| GET | `/jdy-fi/{dbId}/bs/v1/guide-setting/list` | scope | total, data |
| GET | `/jdy-fi/{dbId}/bs/v1/sp/CardVchOption` | key | str |
| GET | `/jdy-fi/{dbId}/bs/v1/sp/InvoiceVchOption1` | key | str |
| GET | `/jdy-fi/{dbId}/bs/v1/sp/runtime` | dbId | book, ebxV7, module, product, scm |
| GET | `/jdy-fi/{dbId}/bs/v1/user-setting/user` | key | dbId, key, userId, value, remark |
| GET | `/jdy-fi/{dbId}/bs/v1/vch-group` | 无 | rows |
| GET | `/jdy-fi/{dbId}/ca/v1/cashier-account` | showForbid | naturalCur, mCur, rows, isAllBind |
| GET | `/jdy-fi/{dbId}/ca/v1/yqy/getBankList` | type | yqyBanks |
| POST | `/jdy-fi/{dbId}/fa/v1/card/list` | addPeriodBefore, addPeriodFrom, addPeriodTo, addVoucher, beginDateFrom, beginDateTo, clearPeriodFrom, clearPeriodTo, clearVch, deprMethod, deptIds, name, number, page, pageSize, showClean, state, typeIds | totalPage, records, page, rows, depreVoucher |
| GET | `/jdy-fi/{dbId}/fa/v1/change-record` | page, pageSize, periodFrom, periodTo | totalPage, records, page, rows |
| GET | `/jdy-fi/{dbId}/fa/v1/report/depreciation-detail` | dept, page, pageSize, periodFrom, periodTo, showChange, showClean | totalPage, records, page, rows, hasShare |
| GET | `/jdy-fi/{dbId}/fa/v1/report/depreciation-sum` | dept, periodFrom, periodTo, showChange, showClean | totalPage, records, page, rows, hasShare |
| GET | `/jdy-fi/{dbId}/fa/v1/type` | 无 | rows |
| GET | `/jdy-fi/{dbId}/gl/v1/finance/balance` | classId, expand, showCur | isQtyAux, rows |
| GET | `/jdy-fi/{dbId}/gl/v1/item/page` | effective, itemClassId, page, pageSize | rows, records, totalPage, page |
| GET | `/jdy-fi/{dbId}/gl/v1/itemClass` | 无 | rows |
| GET | `/jdy-fi/{dbId}/gl/v1/recent-vouchers` | clearCache | rows |
| GET | `/jdy-fi/{dbId}/gl/v1/voucher/list` | aiMark, dateType, fromPeriod, groupId, isCur, isQtyaux, itemClassId, itemId, page, pageSize, remarkType, sidx, sord, toPeriod, voucherAccountItem, voucherSource | rows, records, totalPage, totalCredit, totalDebit, page, recycleSize |
| GET | `/jdy-fi/{dbId}/gl/v1/voucherTotal/list` | fromDate, fromLevel, groupId, toDate, toLevel | vchs, totalCount, attachmentCount, dmountTotal, cmountTotal |
| GET | `/jdy-fi/{dbId}/index/v1/upgrade` | 无 | url, isUpgrade |
| POST | `/jdy-fi/{dbId}/pay/v1/sheet-list` | fromPeriod, matchCon, page, pageSize, toPeriod | rows, records, totalPage, page, salaryTotal, realSalaryTotal, hasOld |
| GET | `/jdy-fi/{dbId}/pay/v1/sheet/getVchOption` | 无 | value, lastMonth |
| POST | `/jdy-fi/{dbId}/pay/v1/sheet/statMonthPay` | matchCon, yearPeriod | dbId, yearPeriod, items, colName |
| GET | `/jdy-fi/{dbId}/rpt/v1/balance` | yearPeriod | rows, isBalance, isRepeat, isAbsent, isBeginBalance, absentAccounts, repeatedAccounts, isPlTrans, isPreBalByCache, needReload, latestPeriod |
| GET | `/jdy-fi/{dbId}/rpt/v1/balance/item` | yearPeriod | rows |
| GET | `/jdy-fi/{dbId}/rpt/v1/cashflow` | yearPeriod | status, isBalance, main, addendum, checkRes, initCheckRes |
| GET | `/jdy-fi/{dbId}/rpt/v1/cashflow/item` | yearPeriod | rows |
| GET | `/jdy-fi/{dbId}/rpt/v1/item/profit/params` | 无 | itemClassId, itemClassName, items, periodTypes, noHappen |
| GET | `/jdy-fi/{dbId}/rpt/v1/profit` | batchOperate, type, yearPeriod | rows, isBalance, isRepeat, isAbsent, isNonProfitBalance, absentAccounts, repeatedAccounts, nonProfitBalance, editable, isEmpty, needReload, latestPeriod |
| POST | `/jdy-fi/{dbId}/vat/v1/invoice-list` | batchRemark, checkStatus, code, customName, date, electCode, feature, fileName, includeVch, keyType, keyVal, number, page, pageSize, periodFrom, periodTo, periodType, productName, remark, showEntry, spreadEntry, status, type | totalPage, records, page, rows, total, allTotal |
| GET | `/jdy-fi/{dbId}/vat/v1/invoice/zyw/collect-state` | 无 | bool |
| GET | `/jdy-fi/{dbId}/vat/v1/order/info/` | 无 | base |
| GET | `/jdy-fi/{dbId}/vat/v1/order/zwy/tax-auth-status-new` | 无 | verifyStatus, verifyMsg, collectionStatus, collectionMsg, invoiceStatus |
| GET | `/jdy-fi/{dbId}/vat/v1/tax-burden` | yearPeriod | cal, out, int, increment, attach |
| GET | `/jdy-fi/{dbId}/vat/v1/voucher-temp-simple` | feature, includeDiyType | rows, records |
| GET | `/pay/employee.do?m=getEmpSign` | m | jobNum, idenCard, name, mobile |
| GET | `/pay/salaryKind.do?m=list` | m | items |
