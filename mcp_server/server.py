"""vn-market-mcp MCP server: stdio transport, read-only + run_analysis tools.

Run directly: python -m mcp_server.server
Connects to Postgres as mcp_ro (read tools) / pipeline_rw (write tools) —
see .env.example for MCP_RO_DATABASE_URL / PIPELINE_RW_DATABASE_URL.
"""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_server.tools.digests import get_market_digest_input_tool, get_weekly_digest_input_tool
from mcp_server.tools.explain import explain_run_tool
from mcp_server.tools.history import query_history_tool
from mcp_server.tools.positions import clear_position_tool, set_position_tool
from mcp_server.tools.predictions import list_predictions_tool
from mcp_server.tools.run_analysis import get_job_status_tool, run_analysis_tool
from mcp_server.tools.snapshot import get_snapshot_tool
from mcp_server.tools.stock_report import get_stock_report_tool, save_commentary_tool
from mcp_server.tools.stats import get_stats_tool
from mcp_server.tools.watchlist import list_watchlist_tool, unwatch_ticker_tool, watch_ticker_tool

mcp = FastMCP("vn-market-mcp")


@mcp.tool()
def run_analysis(ticker: str, style: str = "long", depth: str = "quick") -> dict[str, Any]:
    """Xếp hàng chạy pipeline phân tích cho một mã, trả job_id ngay (không chặn).

    Dùng get_job_status(job_id) để theo dõi, hoặc explain_run/get_snapshot
    sau khi job xong (worker gửi kết quả qua Telegram).
    """
    return run_analysis_tool(ticker, style=style, depth=depth)


@mcp.tool()
def get_job_status(job_id: str) -> dict[str, Any]:
    """Tra trạng thái một job đã xếp hàng bằng run_analysis (queued/running/done/failed)."""
    return get_job_status_tool(job_id)


@mcp.tool()
def get_snapshot(ticker: str | None = None, run_id: str | None = None) -> dict[str, Any]:
    """Đọc lại snapshot đã lưu, theo ticker (mới nhất) hoặc theo run_id (nhãn theo vị thế của người hỏi)."""
    return get_snapshot_tool(ticker=ticker, run_id=run_id)


@mcp.tool()
def query_history(
    ticker: str, series: str, start_date: str | None = None, end_date: str | None = None
) -> dict[str, Any]:
    """Tra cứu chuỗi lịch sử: series là 'prices' | 'fundamentals' | 'foreign_flow'."""
    return query_history_tool(ticker, series, start_date=start_date, end_date=end_date)


@mcp.tool()
def explain_run(run_id: str) -> dict[str, Any]:
    """Chi tiết một run: thông tin run, các data-quality check, các prediction đã ghi."""
    return explain_run_tool(run_id)


@mcp.tool()
def list_predictions(
    ticker: str | None = None, status: str | None = None, limit: int = 20
) -> dict[str, Any]:
    """Liệt kê predictions, lọc theo ticker/status."""
    return list_predictions_tool(ticker=ticker, status=status, limit=limit)


@mcp.tool()
def get_stats() -> dict[str, Any]:
    """Thống kê tổng hợp: tổng số run, phân bố action_label, phân bố status."""
    return get_stats_tool()


@mcp.tool()
def set_position(ticker: str, avg_cost: float | None, declared_by: str | None = None) -> dict[str, Any]:
    """Tự khai đang nắm giữ một mã (không suy luận từ dữ liệu khác). Vị thế là riêng của từng người;
    với người đang giữ, nhãn họ thấy chuyển thành hold/reduce_exit."""
    return set_position_tool(ticker, avg_cost, declared_by)


@mcp.tool()
def clear_position(ticker: str, declared_by: str | None = None) -> dict[str, Any]:
    """Tự khai đã thoát vị thế một mã."""
    return clear_position_tool(ticker, declared_by)


@mcp.tool()
def watch_ticker(ticker: str, declared_by: str | None = None) -> dict[str, Any]:
    """Thêm một mã (kể cả ngoài VN30) vào danh sách theo dõi của người dùng declared_by.

    Mã theo dõi được phân tích tự động cùng VN30 mỗi phiên; lần thêm xếp hàng một lần
    phân tích ngay (data.job_id). status="not_found" nếu mã không niêm yết."""
    return watch_ticker_tool(ticker, declared_by)


@mcp.tool()
def unwatch_ticker(ticker: str, declared_by: str | None = None) -> dict[str, Any]:
    """Bỏ một mã khỏi danh sách theo dõi của người dùng declared_by."""
    return unwatch_ticker_tool(ticker, declared_by)


@mcp.tool()
def list_watchlist(declared_by: str | None = None) -> dict[str, Any]:
    """Các mã người dùng declared_by đang theo dõi, kèm nhãn hành động gần nhất của từng mã."""
    return list_watchlist_tool(declared_by)


@mcp.tool()
def get_stock_report(ticker: str, run_id: str) -> dict[str, Any]:
    """Báo cáo phân tích đầy đủ số liệu (kỹ thuật, cơ bản, khối ngoại, VN-Index, kế hoạch rủi ro) dựng bằng code từ run_id.

    Nếu data.needs_commentary=False: gửi nguyên văn data.final cho người dùng.
    Nếu True: viết đoạn nhận định ngắn, gọi save_commentary, rồi gửi data.report + nhận định.
    """
    return get_stock_report_tool(ticker, run_id)


@mcp.tool()
def save_commentary(ticker: str, run_id: str, commentary: str) -> dict[str, Any]:
    """Kiểm tra rồi lưu đoạn nhận định vừa viết (dùng lại khi dữ liệu không đổi).

    status='saved' → được gửi cho người dùng. status='rejected' → KHÔNG gửi; sửa đúng các lỗi trong
    data.issues rồi gọi lại (số liệu phải có trong báo cáo, không lạc quan hơn nhãn, không từ ngữ nội bộ).
    """
    return save_commentary_tool(ticker, run_id, commentary)


@mcp.tool()
def get_market_digest_input() -> dict[str, Any]:
    """Dữ kiện cho bản tin trước phiên: VN-Index, biến động VN30 phiên gần nhất, khối ngoại, tin qua đêm."""
    return get_market_digest_input_tool()


@mcp.tool()
def get_weekly_digest_input() -> dict[str, Any]:
    """Dữ kiện cho bản tin tuần: VN-Index, VN30 tăng/giảm 5 phiên, khối ngoại, phân bố nhãn, tin trong tuần."""
    return get_weekly_digest_input_tool()


if __name__ == "__main__":
    mcp.run(transport="stdio")
