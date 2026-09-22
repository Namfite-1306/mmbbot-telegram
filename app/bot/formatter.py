from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from app.models import Action, Signal, SignalStatus
from app.cafef_news import NewsItem

if TYPE_CHECKING:
    from app.services.signal_service import SignalLookup


DISCLAIMER = "⚠️ Chỉ phục vụ mục đích học tập, không phải khuyến nghị đầu tư."
ACTION_ICON = {Action.BUY: "🟢", Action.SELL: "🔴", Action.HOLD: "🟡"}


def _format_time(value: datetime, timezone_name: str) -> str:
    return value.astimezone(ZoneInfo(timezone_name)).strftime("%H:%M %d/%m/%Y")


def _score_label(signal: Signal) -> str:
    components = signal.score_components
    if components and any(components.get(key) is None for key in ("T", "F", "M")):
        return f"T kỹ thuật {signal.score:.0f}/100 (chưa có điểm tổng)"
    return f"{signal.score:.0f}/100"


def format_signal(signal: Signal, timezone_name: str = "Asia/Ho_Chi_Minh") -> str:
    if signal.status is SignalStatus.INSUFFICIENT_DATA:
        details = "\n".join(f"• {reason}" for reason in signal.reasons)
        return f"🔴 {signal.ticker} — Không đủ dữ liệu\n\n{details}\n\n{DISCLAIMER}"
    if signal.status is SignalStatus.STALE_DATA:
        details = "\n".join(f"• {reason}" for reason in signal.reasons)
        return f"🔴 {signal.ticker} — Dữ liệu đã cũ; điểm tham khảo {_score_label(signal)}\nDữ liệu: {_format_time(signal.data_time, timezone_name)}\n\n{details}\n\n{DISCLAIMER}"
    if signal.status is SignalStatus.WATCH_ONLY:
        details = "\n".join(f"• {reason}" for reason in signal.reasons)
        price = f"{signal.price:,.0f}".replace(",", ".") if signal.price > 0 else "không có"
        return (f"🟡 {signal.ticker} — Theo dõi, chưa phát lệnh\n"
                f"Giá đóng cửa: {price} VNĐ\nĐiểm tham khảo: {_score_label(signal)}\n"
                f"Dữ liệu: {_format_time(signal.data_time, timezone_name)}\n\n{details}\n\n{DISCLAIMER}")
    if signal.status is SignalStatus.PROVIDER_ERROR:
        return f"🔴 {signal.ticker} — Lỗi dữ liệu\n\nVui lòng thử lại sau."

    icon = ACTION_ICON[signal.action]
    reasons = "\n".join(f"• {reason}" for reason in signal.reasons)
    formatted_price = f"{signal.price:,.0f}".replace(",", ".")
    return (
        f"{icon} {signal.ticker} — {signal.action.value}\n\n"
        f"Giá phân tích: {formatted_price} VNĐ\n"
        f"Điểm tín hiệu: {_score_label(signal)}\n\n"
        f"Lý do:\n{reasons}\n\n"
        f"Dữ liệu cập nhật: {_format_time(signal.data_time, timezone_name)}\n"
        f"Khung thời gian: {signal.timeframe}\n"
        f"Chiến lược: {signal.strategy_version}\n\n"
        f"{DISCLAIMER}"
    )


def format_lookup_error(ticker: str, error_code: str) -> str:
    if error_code == "not_found":
        return f"Không tìm thấy mã {ticker} trong nguồn dữ liệu hiện tại."
    if error_code == "invalid_signal":
        return f"⚠️ Signal của {ticker} không hợp lệ. Vui lòng thử lại sau."
    return f"⚠️ Không thể lấy signal cho {ticker}. Vui lòng thử lại sau."


def format_lookup_results(results: list[SignalLookup], timezone_name: str) -> str:
    messages = [
        format_signal(result.signal, timezone_name)
        if result.signal
        else format_lookup_error(result.ticker, result.error_code or "provider_error")
        for result in results
    ]
    return "\n\n──────────\n\n".join(messages)


def _metric(value: object, digits: int = 2) -> str:
    try:
        number = float(value)
        if not (-float("inf") < number < float("inf")):
            return "chưa có"
        return f"{number:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "chưa có"


def _billions(value: object) -> str:
    try:
        return _metric(float(value) / 1e9)
    except (TypeError, ValueError):
        return "chưa có"


def _percent_ratio(value: object) -> str:
    try:
        return _metric(float(value) * 100)
    except (TypeError, ValueError):
        return "chưa có"


def format_stock_overview(signal: Signal, overview: dict, news: list[NewsItem],
                          live_trade: dict | None = None) -> str:
    quote = overview.get("quote") or {}
    financial = overview.get("financial") or {}
    previous = overview.get("previous_close")
    date_label = (quote.get("trade_date") or signal.data_time.date().isoformat())
    if signal.status is SignalStatus.SUCCESS:
        icon = ACTION_ICON[signal.action]
        status = {Action.BUY: "MUA", Action.SELL: "BÁN", Action.HOLD: "THEO DÕI"}[signal.action]
    elif signal.status is SignalStatus.WATCH_ONLY:
        icon, status = "🟡", "THEO DÕI — chưa phát lệnh"
    else:
        icon, status = "🔴", "CHƯA CÓ TÍN HIỆU — kiểm tra dữ liệu"
    lines = [f"{icon} TÍN HIỆU: {status} | {signal.ticker}"]
    if live_trade:
        moment = live_trade["time"]
        reference = live_trade.get("reference_close")
        change = 100 * (live_trade["price_vnd"] / reference - 1) if reference and reference > 0 else None
        lines.extend(["", "⚡ Dữ liệu mới nhất",
                      f"Giá: {_metric(live_trade['price_vnd'], 0)} VNĐ"
                      + (f" ({change:+.2f}% so với đóng cửa phiên trước)" if change is not None else ""),
                      f"Thời điểm dữ liệu: {moment:%H:%M:%S %d/%m/%Y}"])
        if "total_volume" in live_trade:
            lines.append(f"Khối lượng tích lũy: {_metric(live_trade['total_volume'], 0)} cp")
    else:
        lines.extend(["", "Giá real-time chưa có; dùng dữ liệu đã lưu."])
    lines.extend(["", f"📊 Phiên đã lưu {date_label}"
             + (" (tạm tính)" if quote and not quote.get("is_final") else "")])
    if quote:
        change = (100 * (quote["close"] / previous - 1)) if previous and previous > 0 else None
        lines.extend([
            f"{'Đóng cửa' if quote.get('is_final') else 'Giá lưu gần nhất'}: {_metric(quote.get('close'), 0)} VNĐ"
            + (f" ({change:+.2f}%)" if change is not None else ""),
            f"O/H/L/C: {_metric(quote.get('open'), 0)} / {_metric(quote.get('high'), 0)} / "
            f"{_metric(quote.get('low'), 0)} / {_metric(quote.get('close'), 0)} VNĐ",
            f"Khối lượng: {_metric(quote.get('volume'), 0)} cp",
        ])
    else:
        lines.append("Chưa có giá hợp lệ trong kho dữ liệu.")
    lines.extend(["", "💰 Tài chính cơ bản"])
    if financial:
        period = financial.get("report_period") or "không rõ kỳ"
        announced = financial.get("announcement_date") or "không rõ ngày"
        lines.extend([
            f"BCTC: {period}, công bố {announced}",
            f"Doanh thu: {_billions(financial.get('revenue'))} tỷ VNĐ; "
            f"LNST: {_billions(financial.get('net_profit'))} tỷ VNĐ",
            f"EPS: {_metric(financial.get('eps'), 0)} VNĐ; P/E: {_metric(financial.get('pe'))}; P/B: {_metric(financial.get('pb'))}",
            f"ROE: {_percent_ratio(financial.get('roe'))}%; "
            f"Nợ/VCSH: {_percent_ratio(financial.get('debt_to_equity'))}%",
        ])
    else:
        lines.append("Chưa có BCTC đã công bố trước ngày phân tích.")
    lines.extend(["", f"🎯 Điểm (phiên {signal.data_time:%d/%m/%Y})"])
    components = signal.score_components or {}
    if components:
        lines.append("T: " + _metric(components.get("T"), 0) + "/100 · F: "
                     + _metric(components.get("F"), 0) + "/100 · M: "
                     + _metric(components.get("M"), 0) + "/100")
        if all(components.get(key) is not None for key in ("T", "F", "M")):
            lines.append(f"Tổng (37,5% T + 31,25% F + 31,25% M): {_metric(signal.score, 1)}/100")
        else:
            lines.append("Chưa có điểm tổng; không dùng điểm T thay cho T/F/M.")
    else:
        lines.append("Chưa tính được điểm T/F/M.")
    if signal.reasons:
        lines.append("Lý do: " + "; ".join(signal.reasons[:3]))
    risks = [reason for reason in signal.reasons if any(term in reason.lower() for term in
             ("thiếu", "chưa có", "chưa tính", "cũ", "không làm mới", "unknown", "giá thô", "upcom"))]
    extra_risks = [reason for reason in risks if reason not in signal.reasons[:3]]
    if extra_risks:
        lines.extend(["", "⚠️ Cần chú ý"])
        lines.extend(f"• {reason}" for reason in extra_risks[:2])
    if financial.get("data_quality_flags"):
        lines.append(f"• Cờ chất lượng BCTC: {financial['data_quality_flags']}")
    if news:
        lines.extend(["", "📰 Tin liên quan"])
        lines.extend(f"• {item.title[:110]}: {item.url}" for item in news[:2])
    lines.extend(["", DISCLAIMER])
    return "\n".join(lines)[:4000]


def format_usage(command: str, example: str) -> str:
    return f"Cú pháp: /{command} {example}"


def format_plain_text_help() -> str:
    return "Tôi chưa nhận ra danh sách mã cổ phiếu. Dùng /help để xem hướng dẫn."


def format_limit_notice(tickers: list[str]) -> str:
    return f"Đã bỏ qua do vượt giới hạn mỗi tin nhắn: {', '.join(tickers)}"


def format_alert_state(enabled: bool) -> str:
    if enabled:
        return "Đã bật cảnh báo tự động cho danh mục."
    return "Đã tắt cảnh báo. Danh mục của bạn vẫn được giữ nguyên."


def format_start() -> str:
    return (
        "Chào bạn, tôi là nhà tư vấn chiến lược Mr. Mission Bossible bot. Tôi có thể giúp gì cho bạn.\n\n"
        "• /analyze <MÃ> (hoặc /soi <MÃ>) — xem một mã\n"
        "• /scan — quét BUY/SELL\n"
        "• /add <MÃ> <MÃ> — thêm vào danh mục\n"
        "• /portfolio — xem danh mục\n"
        "• /modelportfolio — xem danh mục mô phỏng theo vốn cấu hình\n"
        "• /paper — tài khoản giao dịch ảo, lệnh và vị thế\n"
        "• /chart <MÃ> — xem biểu đồ\n"
        "• /help — hướng dẫn đầy đủ\n\n"
        f"{DISCLAIMER}"
    )


def format_help() -> str:
    return (
        "Hướng dẫn sử dụng\n\n"
        "/analyze <MÃ> (hoặc /soi <MÃ>) — xem phân tích một mã\n"
        "/chart <MÃ> — xem biểu đồ một năm\n"
        "/scan — xem các signal BUY/SELL\n"
        "/add <MÃ> <MÃ> — thêm tối đa 10 mã\n"
        "/remove <MÃ> <MÃ> — xóa mã\n"
        "/portfolio — xem tổng quan danh mục\n"
        "/modelportfolio — xem phân bổ vốn mô phỏng từ tín hiệu cuối ngày\n"
        "/paper — xem tiền mặt, vị thế và tài sản ảo\n"
        "/paperbuy <MÃ> <SỐ_CP> — xếp lệnh mua ảo (lô 100)\n"
        "/papersell <MÃ> <SỐ_CP> — xếp lệnh bán ảo\n"
        "/paperorders — lệnh ảo đang chờ; /paperhistory — lịch sử\n"
        "/papercancel <ID> — hủy lệnh ảo đang chờ\n"
        "/alert on|off — bật hoặc tắt cảnh báo\n"
        "/status — trạng thái hệ thống\n\n"
        "Bạn cũng có thể gửi trực tiếp một hoặc nhiều mã cổ phiếu.\n\n"
        f"{DISCLAIMER}"
    )


def format_scan(signals: list[Signal], timezone_name: str,
                breadth: tuple[int, int] | None = None, refresh_error: str | None = None,
                scan_date: str | None = None) -> str:
    if not signals:
        return "Chưa có kết quả quét. Kiểm tra dữ liệu phiên trước trong database bằng /status."
    ready = [signal for signal in signals if signal.status is SignalStatus.WATCH_ONLY]
    buys = [signal for signal in signals if signal.status is SignalStatus.SUCCESS and signal.action is Action.BUY]
    sells = [signal for signal in signals if signal.status is SignalStatus.SUCCESS and signal.action is Action.SELL]
    stale = sum(signal.status is SignalStatus.STALE_DATA for signal in signals)
    missing = sum(signal.status is SignalStatus.INSUFFICIENT_DATA for signal in signals)
    errors = sum(signal.status is SignalStatus.PROVIDER_ERROR for signal in signals)
    lines = [f"🔎 Đã quét {len(signals)} mã toàn thị trường",
             f"BUY: {len(buys)} | SELL: {len(sells)} | Theo dõi: {len(ready)} | Dữ liệu cũ: {stale} | Chưa đủ: {missing} | Lỗi: {errors}"]
    if scan_date:
        lines.insert(1, f"Phiên dữ liệu: {scan_date} (đã lưu; /scan không crawl)")
    if breadth and breadth[1]:
        lines.append(f"Breadth HOSE: {breadth[0]}/{breadth[1]} mã tăng ({100*breadth[0]/breadth[1]:.1f}%)")
    if refresh_error:
        lines.append(f"Dữ liệu chưa được làm mới: {refresh_error[:120]}")
    for label, icon, group in (("BUY", "🟢", buys), ("SELL", "🔴", sells)):
        lines.append(f"\n{icon} {label} (top 10):")
        lines.extend(f"{icon} {signal.ticker}: {_score_label(signal)}"
                     for signal in sorted(group, key=lambda item: (-item.score, item.ticker))[:10])
    lines.extend(["", "Tín hiệu tính tại Close; lệnh mô phỏng ở Open phiên sau.", DISCLAIMER])
    return "\n".join(lines)


def format_watchlist(items: list[tuple[str, Signal | None, str | None, dict | None]]) -> str:
    if not items:
        return "Danh mục của bạn đang trống. Dùng /add <MÃ> để thêm mã.\n\n/soi <MÃ> — xem chi tiết · /help — hướng dẫn"
    lines = [f"📋 Danh mục quan tâm ({len(items)}/10)", "Màu là tóm tắt trạng thái chiến lược, không phải khuyến nghị giao dịch."]
    for ticker, signal, error_code, overview in items:
        quote = (overview or {}).get("quote") or {}
        previous = (overview or {}).get("previous_close")
        if signal and signal.status is SignalStatus.SUCCESS and signal.action is Action.BUY:
            icon, label, note = "🟢", "Tích cực", "Đạt điều kiện BUY của chiến lược ở phiên đã chốt."
        elif signal and signal.status is SignalStatus.SUCCESS and signal.action is Action.SELL:
            icon, label, note = "🔴", "Cần chú ý", "Có điều kiện thoát nếu đang nắm giữ."
        elif signal and signal.status is SignalStatus.WATCH_ONLY:
            icon, label, note = "🟡", "Trung tính", "Chưa đủ điều kiện phát BUY/SELL mới."
            if any("thiếu" in reason.lower() or "chưa có" in reason.lower() for reason in signal.reasons):
                note = "Còn thiếu điều kiện hoặc dữ liệu để xác nhận tín hiệu."
        elif signal and signal.status is SignalStatus.STALE_DATA:
            icon, label, note = "🔴", "Cần chú ý", "Dữ liệu đã cũ; cần cập nhật trước khi đánh giá."
        else:
            icon, label = "🔴", "Cần chú ý"
            note = ("Không tìm thấy mã trong nguồn dữ liệu." if error_code == "not_found"
                    else "Chưa đủ dữ liệu để đánh giá." if signal else "Không đọc được dữ liệu của mã này.")
        price = quote.get("close") if quote else (signal.price if signal and signal.price > 0 else None)
        percent = 100 * (price / previous - 1) if price and previous and previous > 0 else None
        day = quote.get("trade_date")
        price_line = f"Giá: {_metric(price, 0)} VNĐ | Thay đổi: {percent:+.2f}%" if percent is not None else f"Giá: {_metric(price, 0)} VNĐ | Thay đổi: chưa có"
        if day:
            price_line += f" ({day}{', tạm tính' if not quote.get('is_final') else ''})"
        lines.extend(["", f"{icon} {ticker} — {label}", price_line, f"Điểm đáng chú ý: {note}"])
    lines.extend(["", "/soi <MÃ> — xem chi tiết · /help — hướng dẫn"])
    return "\n".join(lines)


def format_add_result(added: list[str], existing: list[str], invalid: list[str], full: list[str]) -> str:
    lines = []
    if added:
        lines.append(f"Đã thêm: {', '.join(added)}")
    if existing:
        lines.append(f"Đã có: {', '.join(existing)}")
    if invalid:
        lines.append(f"Không hợp lệ/không hỗ trợ: {', '.join(invalid)}")
    if full:
        lines.append(f"Không thêm do danh mục đã đủ 10 mã: {', '.join(full)}")
    return "\n".join(lines) or "Không có mã nào để thêm."


def format_delete_result(removed: list[str], missing: list[str], invalid: list[str]) -> str:
    lines = []
    if removed:
        lines.append(f"Đã xóa: {', '.join(removed)}")
    if missing:
        lines.append(f"Không có trong danh mục: {', '.join(missing)}")
    if invalid:
        lines.append(f"Mã không hợp lệ: {', '.join(invalid)}")
    return "\n".join(lines) or "Không có mã nào để xóa."


def format_status(provider: str, database_ok: bool, server_time: datetime, data_time: str) -> str:
    return (
        "Trạng thái hệ thống\n\n"
        "Bot: Đang hoạt động\n"
        f"Signal Provider: {provider}\n"
        f"Database: {'Hoạt động' if database_ok else 'Không khả dụng'}\n"
        f"Thời gian server: {server_time.strftime('%H:%M:%S %d/%m/%Y %Z')}\n"
        f"Dữ liệu provider: {data_time}"
    )
