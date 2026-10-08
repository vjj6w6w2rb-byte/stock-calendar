#!/usr/bin/env python3
"""把订阅日历中的所有定时事件统一转换为美国东部时间。

全天事件保持日期不变；定时事件统一使用 America/New_York，自动处理 EST/EDT 夏令时。
同时修正日历时区元数据以及说明文字中残留的“北京时间”。
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from icalendar import Calendar, Timezone

OUTPUT = Path("stocks.ics")
ET = ZoneInfo("America/New_York")


def replace_datetime_property(component, name: str) -> datetime | date | None:
    if name not in component:
        return None
    value = component.decoded(name)
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        # 旧文件中的无时区时间按日历原先的值处理；目前生成器都会带时区。
        converted = value.replace(tzinfo=ET)
    else:
        converted = value.astimezone(ET)
    del component[name]
    component.add(name.lower(), converted)
    return converted


def main() -> None:
    if not OUTPUT.exists():
        raise SystemExit("stocks.ics 不存在")

    cal = Calendar.from_ical(OUTPUT.read_bytes())

    # 删除旧 VTIMEZONE，最后统一加入 America/New_York。
    cal.subcomponents = [c for c in cal.subcomponents if getattr(c, "name", "") != "VTIMEZONE"]
    if "x-wr-timezone" in cal:
        del cal["x-wr-timezone"]
    cal.add("x-wr-timezone", "America/New_York")

    for component in cal.walk("VEVENT"):
        start = replace_datetime_property(component, "DTSTART")
        replace_datetime_property(component, "DTEND")

        if "DTSTAMP" in component:
            stamp = component.decoded("DTSTAMP")
            if isinstance(stamp, datetime):
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=ET)
                else:
                    stamp = stamp.astimezone(ET)
                del component["DTSTAMP"]
                component.add("dtstamp", stamp)

        description_prop = component.get("description")
        if description_prop is not None:
            description = str(description_prop)
            if isinstance(start, datetime):
                et_text = f"美东时间：{start.astimezone(ET):%Y-%m-%d %H:%M}"
                description = re.sub(
                    r"北京时间：\d{4}-\d{2}-\d{2} \d{2}:\d{2}",
                    et_text,
                    description,
                )
            # 美股提前收市说明原本同时写了北京时间，统一只保留美东时间。
            description = re.sub(r"；北京时间 \d{2}:\d{2}", "", description)
            del component["description"]
            component.add("description", description)

    cal.add_component(Timezone.from_tzid("America/New_York"))
    OUTPUT.write_bytes(cal.to_ical())


if __name__ == "__main__":
    main()
