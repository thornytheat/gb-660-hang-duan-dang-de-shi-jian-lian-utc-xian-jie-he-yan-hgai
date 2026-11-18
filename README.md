# 航班时刻运行台

面向真实数据复核的中型数据工程工具，包含六个可独立调用的业务模块。规则、失败原因与证据字段见 docs/DATA-DICTIONARY.md，陷阱和测试映射见 docs/DEPTH.md。

## 启动

docker compose up --build

浏览器访问 http://localhost:18219。测试：docker compose exec -T app pytest -q

## 模块

- M1 航段采信：页面 /legs，接口 /api/legs/import
- M2 机场时区：页面 /airports，接口 /api/airports
- M3 当地时刻：页面 /local-time，接口 /api/local-time/resolve
- M4 UTC 航链：页面 /itineraries，接口 /api/itineraries/build
- M5 衔接判定：页面 /connections，接口 /api/connections/check
- M6 批次导出：页面 /batches，接口 /api/reconcile
- M7 计费对账：页面 /billing，接口 /api/billing/runs（JSON 或 multipart CSV 上传，对账单见 /api/billing/export/statement.csv）

## 运行规则

同一输入内容生成相同运行标识，重复执行复用已有批次。隔离记录包含原因码、原始行号与原值；业务导出只保留一份证据清单。

## 计费对账（M7）

- 共享航班号映射到执飞航班号后归并为同一架次，一次起飞只收一次费；航班性质进架次键，同航线同刻加班机另算。
- 两条航段都缺执飞航班号时保持 UNVERIFIED 挂账，只给疑似重复提示，绝不猜测并单；未结清航段可在 `/api/billing/unsettled` 查询。
- 导入的共享关系会改变架次归并；同输入重跑幂等，纠正重算时历史已收费架次标记 ALREADY_BILLED，DB 部分唯一索引兜底防重复收费。
- 对账单每个计费架次带全部原航班号与物理行号，可逐架次回溯。样例见 `sample_data/billing_legs.csv`、`sample_data/billing_codeshares.csv`。

