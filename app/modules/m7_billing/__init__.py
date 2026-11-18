"""M7 计费对账：按实际起飞架次与航司对账。

设计原则（对应业务规则）：
- 同一物理起飞的共享航班号按执飞航班号归并，只产生一个计费架次。
- 航班性质（NORMAL/EXTRA）参与架次键，同航线同刻加班机另算。
- 两条航段都缺执飞航班号时不猜测、不自动并单，只做疑似重复提示并挂账未结清。
- 导入的共享航班关系可补全执飞身份，从而改变架次分组结果。
- 已计费架次键持久化，重跑同批幂等，纠正后重算也不重复收费。
"""

# M7 专用原因码，与 app.reasons.REASONS 中的中文说明对应
REASON_EMPTY = "EMPTY_VALUE"
REASON_BAD_TIMESTAMP = "BAD_TIMESTAMP"
REASON_BAD_NATURE = "BAD_FLIGHT_NATURE"
REASON_DUPLICATE = "DUPLICATE_KEY"
REASON_CONFLICT = "VALUE_CONFLICT"
REASON_MAPPING_CONFLICT = "MAPPING_CONFLICT"
REASON_UNKNOWN_REFERENCE = "UNKNOWN_REFERENCE"
REASON_MISSING_OPERATING = "OPERATING_FLIGHT_NO_MISSING"
REASON_REG_CONFLICT = "AIRCRAFT_REG_CONFLICT"
