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
        return "chưa tính được"
    return f"{signal.score:.0f}/100"


def _public_reasons(reasons: list[str]) -> list[str]:
    """Hide internal score components while retaining useful data warnings."""
    public: list[str] = []
    for reason in reasons:
        if reason.startswith("T="):
            continue
        if reason.startswith("Chưa có điểm tổng T/F/M"):
            item = "Chưa đủ dữ liệu đầu vào để tính điểm."
        elif reason.startswith(("F chưa tính được:", "M chưa tính được:")):
            item = "Chưa tính được điểm: " + reason.partition(":")[2].strip()
        elif reason.startswith("Điểm đạt 75 nhưng"):
            item = "Điểm đạt ngưỡng nhưng điều kiện vào lệnh hoặc dữ liệu chưa hợp lệ."
        else:
            item = reason
        if item not in public:
            public.append(item)
    return public


def format_signal(signal: Signal, timezone_name: str = "Asia/Ho_Chi_Minh") -> str:
    if signal.status is SignalStatus.INSUFFICIENT_DATA:
        details = "\n".join(f"• {reason}" for reason in _public_reasons(signal.reasons))
        return f"🔴 {signal.ticker} — Không đủ dữ liệu\n\n{details}\n\n{DISCLAIMER}"
    if signal.status is SignalStatus.STALE_DATA:
        details = "\n".join(f"• {reason}" for reason in _public_reasons(signal.reasons))
        return f"🔴 {signal.ticker} — Dữ liệu đã cũ; điểm tham khảo {_score_label(signal)}\nDữ liệu: {_format_time(signal.data_time, timezone_name)}\n\n{details}\n\n{DISCLAIMER}"
    if signal.status is SignalStatus.WATCH_ONLY:
        details = "\n".join(f"• {reason}" for reason in _public_reasons(signal.reasons))
        price = f"{signal.price:,.0f}".replace(",", ".") if signal.price > 0 else "không có"
        return (f"🟡 {signal.ticker} — Theo dõi, chưa phát lệnh\n"
                f"Giá đóng cửa: {price} VNĐ\nĐiểm tham khảo: {_score_label(signal)}\n"
                f"Dữ liệu: {_format_time(signal.data_time, timezone_name)}\n\n{details}\n\n{DISCLAIMER}")
    if signal.status is SignalStatus.PROVIDER_ERROR:
        return f"🔴 {signal.ticker} — Lỗi dữ liệu\n\nVui lòng thử lại sau."

    icon = ACTION_ICON[signal.action]
    reasons = "\n".join(f"• {reason}" for reason in _public_reasons(signal.reasons))
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
        return (f"Không lấy được dữ liệu cho mã {ticker}: "
                "không tìm thấy trong nguồn dữ liệu hiện tại.")
    if error_code == "invalid_signal":
        return (f"⚠️ Không lấy được dữ liệu cho mã {ticker}: "
                "signal không hợp lệ. Vui lòng thử lại sau.")
    return (f"⚠️ Không lấy được dữ liệu cho mã {ticker}. "
            "Vui lòng thử lại sau.")


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


def _decision_explanation(signal: Signal) -> str:
    if signal.status in {SignalStatus.STALE_DATA, SignalStatus.INSUFFICIENT_DATA,
                         SignalStatus.PROVIDER_ERROR}:
        return "Cần dữ liệu hợp lệ đúng phiên trước khi đánh giá điều kiện mua."
    if signal.action is Action.BUY:
        return "Đã qua các cổng BUY của chiến lược tại giá đóng cửa; không phải lệnh khớp thực tế."
    if signal.action is Action.SELL:
        return "Điều kiện thoát vị thế được xác nhận tại giá đóng cửa."
    notes: list[str] = []
    components = signal.score_components or {}
    if components and any(components.get(key) is None for key in ("T", "F", "M")):
        notes.append("Chưa đủ dữ liệu đầu vào để tính điểm")
    elif signal.score < 75:
        notes.append(f"Điểm tổng còn {75 - signal.score:.1f} điểm để chạm ngưỡng 75")
    for reason in signal.reasons:
        if reason.startswith("Cổng vào lệnh: "):
            entry = reason.partition(": ")[2].split()[0]
            if entry != "ENTRY":
                notes.append(f"Cổng vào lệnh hiện là {entry}")
            break
        if "; E=" in reason:
            entry = reason.split("; E=", 1)[1].split()[0]
            if entry != "ENTRY":
                notes.append(f"Cổng vào lệnh hiện là {entry}")
            break
    for reason in signal.reasons:
        if reason.startswith("Market regime: "):
            regime = reason.partition(": ")[2]
            if regime not in {"BULL", "NEUTRAL"}:
                notes.append(f"Trạng thái thị trường hiện là {regime}")
            break
    if not notes:
        notes.append("Chưa qua đủ cổng BUY; xem lý do chiến lược bên dưới")
    return "; ".join(notes[:3]) + "."


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
                      f"Thời điểm dữ liệu: {moment:%H:%M:%S %d/%m/%Y}",
                      f"Nguồn giá mới: {live_trade.get('source', 'DNSE')}"])
        if "total_volume" in live_trade:
            lines.append(f"Khối lượng tích lũy: {_metric(live_trade['total_volume'], 0)} cp")
    else:
        lines.extend(["", "Chưa có giá mới hơn từ DNSE; dùng dữ liệu đã lưu."])
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
    if quote and quote.get("source"):
        lines.append(f"Nguồn giá lưu: {str(quote['source']).upper()} · ngày {date_label}")
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
    if not components or all(components.get(key) is not None for key in ("T", "F", "M")):
        lines.append(f"Điểm: {_metric(signal.score, 1)}/100")
    else:
        lines.append("Điểm: chưa tính được.")
    lines.append("Điều kiện tiếp theo: " + _decision_explanation(signal))
    public_reasons = _public_reasons(signal.reasons)
    if public_reasons:
        lines.append("Lý do: " + "; ".join(public_reasons[:3]))
    risks = [reason for reason in public_reasons if any(term in reason.lower() for term in
             ("thiếu", "chưa có", "chưa tính", "cũ", "không làm mới", "unknown", "giá thô", "upcom"))]
    extra_risks = [reason for reason in risks if reason not in public_reasons[:3]]
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
    examples = {
        "soi": "/soi FPT",
        "analyze": "/analyze FPT",
        "chart": "/chart FPT",
        "add": "/add FPT HPG",
        "remove": "/remove FPT",
        "papercapital": "/papercapital 100000000",
        "paperposition": "/paperposition FPT 100 85000",
        "paperbuy": "/paperbuy FPT 100",
        "papersell": "/papersell FPT 100",
        "papercancel": "/papercancel 12 hoặc /papercancel FPT",
        "alert": "/alert on",
        "digest": "/digest on",
    }
    syntax = f"/{command} {example}".strip()
    sample = examples.get(command)
    return (f"Lệnh chưa đúng hoặc còn thiếu thông tin.\nCú pháp đúng: {syntax}"
            + (f"\nVí dụ: {sample}" if sample else ""))


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
        "👋 Chào bạn, tôi là nhà tư vấn chiến lược Mr. Mission Bossible bot. Tôi có thể giúp gì cho bạn.\n\n"
        "🔎 /analyze <MÃ> (hoặc /soi <MÃ>) — xem một mã\n"
        "📡 /scan — xem top điểm cao/thấp trong VN100\n"
        "➕ /add <MÃ> <MÃ> — thêm vào danh mục\n"
        "💼 /portfolio — xem danh mục\n"
        "📊 /modelportfolio — xem danh mục mô phỏng theo vốn cấu hình\n"
        "🎮 /paper — tài khoản giao dịch ảo, lệnh và vị thế\n"
        "🔔 /digest on — tổng kết danh mục theo phiên (tự chọn bật)\n"
        "📈 /chart <MÃ> — xem biểu đồ\n"
        "❓ /help — hướng dẫn đầy đủ\n\n"
        f"{DISCLAIMER}"
    )


def format_help() -> str:
    return (
        "Hướng dẫn sử dụng\n\n"
        "/analyze <MÃ> (hoặc /soi <MÃ>) — xem phân tích một mã\n"
        "/chart <MÃ> — xem biểu đồ một năm\n"
        "/scan — xem top điểm cao/thấp trong các mã VN100 tăng hoặc giảm\n"
        "/add <MÃ> <MÃ> — thêm tối đa 10 mã\n"
        "/remove <MÃ> <MÃ> — xóa mã\n"
        "/portfolio — xem tổng quan danh mục\n"
        "/modelportfolio (hoặc /phanbo) — xem phân bổ vốn mô phỏng từ tín hiệu cuối ngày\n"
        "/paper — xem tiền mặt, vị thế và tài sản ảo\n"
        "/papercapital <VỐN_VNĐ> — đặt lại vốn góp ảo; chênh lệch cộng/trừ tiền mặt\n"
        "/paperposition <MÃ> <SỐ_CP> <GIÁ_VỐN_VNĐ> — nhập/chỉnh vị thế có sẵn; 0 0 để xóa\n"
        "/paperbuy <MÃ> <SỐ_CP> (hoặc /buy, /mua) — mua ảo ngay theo giá API (lô 100)\n"
        "/papersell <MÃ> <SỐ_CP> (hoặc /sell, /ban) — bán ảo ngay theo giá API\n"
        "/paperorders — lệnh chờ cũ; /paperhistory — lịch sử\n"
        "/papercancel <ID hoặc MÃ> — hủy một lệnh hoặc mọi lệnh chờ của mã\n"
        "/alert on|off — bật hoặc tắt cảnh báo\n"
        "/digest on|off — bật hoặc tắt tổng kết danh mục cuối ngày (mặc định tắt)\n"
        "/status — trạng thái hệ thống\n\n"
        "Bạn cũng có thể gửi trực tiếp một hoặc nhiều mã cổ phiếu.\n\n"
        f"{DISCLAIMER}"
    )


def format_scan(signals: list[Signal], timezone_name: str,
                breadth: tuple[int, int] | None = None, refresh_error: str | None = None,
                scan_date: str | None = None,
                coverage: tuple[int, int] | None = None,
                expected_scan_date: str | None = None,
                vn100_mode: bool = False) -> str:
    if not signals:
        message = ("Chưa có mã VN100 nào vừa tính được điểm vừa tăng hoặc giảm trong phiên."
                   if vn100_mode else
                   "Chưa có kết quả quét. Kiểm tra dữ liệu phiên trước trong database bằng /status.")
        if scan_date and expected_scan_date and scan_date != expected_scan_date:
            return (f"⚠️ Chưa có VNINDEX cuối ngày hợp lệ cho {expected_scan_date}; "
                    f"đã thử dữ liệu phiên {scan_date}.\n{message}")
        return message
    if vn100_mode:
        gainers = [signal for signal in signals if signal.change_pct is not None and signal.change_pct > 0]
        losers = [signal for signal in signals if signal.change_pct is not None and signal.change_pct < 0]
        ranked = sorted(gainers + losers, key=lambda item: (-item.score, item.ticker))
        lines = [f"🔎 VN100 — {len(ranked)} mã tính được điểm và có biến động",
                 f"🟢 Đang tăng: {len(gainers)} | 🔴 Đang giảm: {len(losers)}"]
        if scan_date:
            lines.insert(1, f"Phiên dữ liệu: {scan_date}")
        if scan_date and expected_scan_date and scan_date != expected_scan_date:
            lines.insert(1, f"⚠️ Đang dùng phiên {scan_date}, không phải phiên dự kiến {expected_scan_date}.")
        if refresh_error:
            lines.append(f"Dữ liệu chưa được làm mới: {refresh_error[:120]}")

        def row(signal: Signal) -> str:
            icon = "🟢" if signal.change_pct and signal.change_pct > 0 else "🔴"
            return f"{icon} {signal.ticker}: {signal.change_pct:+.2f}% · {signal.score:.2f}/100"

        lines.append("\n🏆 Điểm cao nhất (top 10):")
        lines.extend(row(signal) for signal in ranked[:10])
        lines.append("\n📉 Điểm thấp nhất (bottom 10):")
        lines.extend(row(signal) for signal in sorted(ranked, key=lambda item: (item.score, item.ticker))[:10])
        lines.extend(["", "Chỉ gồm mã VN100 tăng/giảm và tính được điểm.", DISCLAIMER])
        return "\n".join(lines)
    ready = [signal for signal in signals if signal.status is SignalStatus.WATCH_ONLY]
    buys = [signal for signal in signals if signal.status is SignalStatus.SUCCESS and signal.action is Action.BUY]
    sells = [signal for signal in signals if signal.status is SignalStatus.SUCCESS and signal.action is Action.SELL]
    stale = sum(signal.status is SignalStatus.STALE_DATA for signal in signals)
    missing = sum(signal.status is SignalStatus.INSUFFICIENT_DATA for signal in signals)
    errors = sum(signal.status is SignalStatus.PROVIDER_ERROR for signal in signals)
    lines = [f"🔎 Đã quét {len(signals)} mã toàn thị trường",
             f"BUY: {len(buys)} | SELL: {len(sells)} | Theo dõi: {len(ready)} | Dữ liệu cũ: {stale} | Chưa đủ: {missing} | Lỗi: {errors}"]
    if scan_date:
        lines.insert(1, f"Phiên dữ liệu: {scan_date}")
    if scan_date and expected_scan_date and scan_date != expected_scan_date:
        lines.insert(1, f"⚠️ Chưa có VNINDEX cuối ngày hợp lệ cho {expected_scan_date}; "
                        f"đang dùng dữ liệu phiên {scan_date}. Đây không phải tín hiệu của {expected_scan_date}.")
    if breadth and breadth[1]:
        lines.append(f"Breadth HOSE: {breadth[0]}/{breadth[1]} mã tăng ({100*breadth[0]/breadth[1]:.1f}%)")
    if refresh_error:
        lines.append(f"Dữ liệu chưa được làm mới: {refresh_error[:120]}")
    unavailable = [signal.ticker for signal in signals
                   if signal.status in {SignalStatus.STALE_DATA,
                                        SignalStatus.INSUFFICIENT_DATA,
                                        SignalStatus.PROVIDER_ERROR}]
    if unavailable:
        shown = unavailable[:30]
        suffix = f" và {len(unavailable) - len(shown)} mã khác" if len(unavailable) > len(shown) else ""
        lines.append("Không lấy được dữ liệu đúng phiên cho: "
                     f"{', '.join(shown)}{suffix}.")
    for label, icon, group in (("BUY", "🟢", buys), ("SELL", "🔴", sells)):
        lines.append(f"\n{icon} {label} (top 10):")
        lines.extend(f"{icon} {signal.ticker}: {_score_label(signal)}"
                     for signal in sorted(group, key=lambda item: (-item.score, item.ticker))[:10])
    lines.extend(["", "Tín hiệu tính tại Close; lệnh ảo mới dùng giá API tại lúc đặt.", DISCLAIMER])
    return "\n".join(lines)


def format_daily_digest(session: str, items: list[tuple[str, Signal | None]],
                        coverage: tuple[int, int] | None = None) -> str:
    lines = [f"📅 Tổng kết danh mục — phiên {session}",
             "Dữ liệu cuối ngày đã lưu; không phải giá trực tiếp."]
    if coverage and coverage[1]:
        lines.append(f"Độ bao phủ thị trường: {coverage[0]}/{coverage[1]} mã có giá hợp lệ.")
        if coverage[0] < coverage[1]:
            lines.append("⚠️ Một số mã chưa có giá phiên này; không suy diễn thành tín hiệu BÁN.")
    for ticker, signal in items:
        if signal is None or signal.data_time.date().isoformat() != session:
            lines.append(f"⚪ {ticker}: chưa có tín hiệu đúng phiên.")
            continue
        if signal.status is SignalStatus.SUCCESS and signal.action is Action.BUY:
            icon, label = "🟢", "MUA"
        elif signal.status is SignalStatus.SUCCESS and signal.action is Action.SELL:
            icon, label = "🔴", "BÁN"
        elif signal.status is SignalStatus.WATCH_ONLY or signal.action is Action.HOLD:
            icon, label = "🟡", "THEO DÕI"
        else:
            icon, label = "⚪", "THIẾU DỮ LIỆU"
        price = f" · Close {_metric(signal.price, 0)} VNĐ" if signal.price > 0 else ""
        lines.append(f"{icon} {ticker}: {label}{price} · {_score_label(signal)}")
    lines.extend(["", "/soi <MÃ> để xem lý do · /digest off để dừng", DISCLAIMER])
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


def format_status(provider: str, database_ok: bool, server_time: datetime, data_time: str,
                  last_full_eod: str | None = None, expected_eod: str | None = None,
                  refresh_health: dict | None = None) -> str:
    message = (
        "Trạng thái hệ thống\n\n"
        "Bot: Đang hoạt động\n"
        f"Signal Provider: {provider}\n"
        f"Database: {'Hoạt động' if database_ok else 'Không khả dụng'}\n"
        f"Thời gian server: {server_time.strftime('%H:%M:%S %d/%m/%Y %Z')}\n"
        f"Dữ liệu provider: {data_time}"
    )
    if expected_eod:
        message += (f"\nPhiên EOD cần có: {expected_eod}"
                    f"\nCập nhật toàn thị trường đã xác nhận: {last_full_eod or 'chưa có'}")
        if last_full_eod != expected_eod:
            message += "\n⚠️ Dữ liệu toàn thị trường chưa hoàn tất."
    if refresh_health:
        state = refresh_health.get("state", "waiting")
        message += f"\nTác vụ EOD: {state}"
        if refresh_health.get("target"):
            message += f"\nPhiên đang theo dõi: {refresh_health['target']}"
        if refresh_health.get("covered") is not None:
            message += (f"\nMã đã lưu hợp lệ: {refresh_health['covered']}/"
                        f"{refresh_health.get('universe', '?')}"
                        f"; cần ít nhất {refresh_health.get('required', '?')}")
        if refresh_health.get("failed_symbols"):
            message += f"\nMã tải lỗi: {refresh_health['failed_symbols']}"
        if refresh_health.get("missing_symbols"):
            message += f"\nMã còn thiếu: {refresh_health['missing_symbols']}"
        if refresh_health.get("last_success"):
            message += f"\nCập nhật thành công gần nhất: {refresh_health['last_success']}"
        if state == "failed":
            message += f"; thử lại sau khoảng {refresh_health.get('retry_minutes', '?')} phút"
    return message
