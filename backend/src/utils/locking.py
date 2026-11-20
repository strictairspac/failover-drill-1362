"""跨进程互斥锁。生产环境使用 Redis；Redis 不可用时降级为单机线程锁，
保证本地/测试环境（如 SQLite 验证）同样具备互斥语义。"""
import logging
import threading
from contextlib import contextmanager
from src.config.settings import REDIS_URL

_thread_locks = {}
_redis_client = None

def _redis():
    global _redis_client
    if _redis_client is None:
        from redis import Redis
        client = Redis.from_url(REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        try:
            client.ping()
        except Exception:
            logging.getLogger('rollup').warning('Redis 不可达，互斥锁降级为进程内锁')
            _redis_client = False
            return None
        _redis_client = client
    return _redis_client or None

@contextmanager
def named_lock(name, timeout=30):
    client = _redis()
    if client is not None:
        lock = client.lock(name, timeout=timeout, blocking_timeout=1)
        acquired = lock.acquire()
        if not acquired:
            raise LockBusy(name)
        try:
            yield
        finally:
            try:
                lock.release()
            except Exception:
                logging.getLogger('rollup').warning('锁 %s 释放失败，等待 TTL 过期', name)
        return
    local = _thread_locks.setdefault(name, threading.Lock())
    if not local.acquire(timeout=1):
        raise LockBusy(name)
    try:
        yield
    finally:
        local.release()

class LockBusy(Exception):
    def __init__(self, name):
        super().__init__(f'已有补算任务在执行：{name}')
        self.name = name
