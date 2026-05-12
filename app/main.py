import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .database import engine
from . import models
from .routers import stocks, portfolio, trading

# 确保数据目录存在
os.makedirs("data", exist_ok=True)

models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="旺财A股AI量化分析系统", version="1.0.0")

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(stocks.router)
app.include_router(portfolio.router)
app.include_router(trading.router)
