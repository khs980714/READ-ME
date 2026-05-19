"""
Rate limiter 인스턴스 (slowapi)
main.py와 라우터에서 공유합니다.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

# IP 기반 레이트 리밋: 챗봇 API 과부하 방지
limiter = Limiter(key_func=get_remote_address)
