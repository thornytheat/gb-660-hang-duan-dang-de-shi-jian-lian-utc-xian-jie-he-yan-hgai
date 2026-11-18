"""M7 HTTP 接口与页面。"""
import csv
import io

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.web import page
from . import service
from .exporter import statement_csv, statement_json

router = APIRouter()


@router.get("/billing", response_class=HTMLResponse)
def view(request: Request):
    return page(request, "m7.html", "计费对账", "/api/billing/runs", [
        {"key": "计费架次", "status": "按执飞号归并", "value": "共享号只收一次"},
        {"key": "加班机", "status": "性质进架次键", "value": "同刻另算"},
        {"key": "未核实航段", "status": "不猜测并单", "value": "挂账待补号"},
    ])


@router.post("/api/billing/runs", status_code=201)
def create_run(payload: dict | None = None):
    if not payload or payload.get("demo") or not payload.get("legs"):
        return service.demo()
    return service.create_run(payload)


@router.post("/api/billing/runs/csv", status_code=201)
async def create_run_csv(legs: UploadFile, codeshares: UploadFile | None = None):
    leg_rows = list(csv.DictReader(io.StringIO((await legs.read()).decode("utf-8"))))
    mapping_rows = []
    if codeshares is not None:
        mapping_rows = list(csv.DictReader(io.StringIO((await codeshares.read()).decode("utf-8"))))
    return service.create_run({"legs": leg_rows, "codeshares": mapping_rows, "fee_schedule": []})


@router.get("/api/billing/runs/{run_id}")
def get_run(run_id: str):
    run = service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND",
                                                      "message": f"批次 {run_id} 不存在"})
    return run


@router.get("/api/billing/unsettled")
def unsettled():
    return service.unsettled_legs()


@router.get("/api/billing/export/statement.csv")
def export_csv(run_id: str):
    run = service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND",
                                                      "message": f"批次 {run_id} 不存在"})
    return PlainTextResponse(
        statement_csv(run), media_type="text/csv",
        headers={"content-disposition": f'attachment; filename="statement-{run_id}.csv"'})


@router.get("/api/billing/export/statement.json")
def export_json(run_id: str):
    run = service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND",
                                                      "message": f"批次 {run_id} 不存在"})
    return statement_json(run)
