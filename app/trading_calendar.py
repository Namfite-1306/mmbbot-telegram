"""Vietnam equity trading-day cutoffs from exchange-published closures.

2026 dates: HNX annual notice (03/12/2025) and the HSX/VNX update
(25/12/2025) adding 02/01/2026. Saturdays remain closed even on makeup days.
https://old.hnx.vn/vi-vn/chi-tiet-lich-nghi-gd-60021971.html?_page=1
https://staticfile.hsx.vn/Uploads/UploadDocuments/2426350/20251225_Thong%20bao%20%20ve%20%20viec%20cap%20nhat%20lich%20nghi%20giao%20dich%20Tet%20Duong%20lich%202026%20toan%20thi%20truong.pdf
"""

from __future__ import annotations

from datetime import date, timedelta


HOLIDAYS_2026 = frozenset({
    date(2026, 1, 1), date(2026, 1, 2),
    *(date(2026, 2, day) for day in range(16, 21)),
    date(2026, 4, 27), date(2026, 4, 30), date(2026, 5, 1),
    date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 2),
})


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in HOLIDAYS_2026


def previous_trading_day(day: date) -> date:
    day -= timedelta(days=1)
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day
