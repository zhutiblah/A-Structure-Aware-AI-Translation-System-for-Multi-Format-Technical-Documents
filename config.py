# config.py
import os
import logging
import xml.etree.ElementTree as ET
from constants import LOG_FILE, AUDIT_LOG_FILE, NAMESPACES

def setup_logging():
    """初始化和配置日志记录器。"""
    if os.path.exists(LOG_FILE):
        try:
            os.remove(LOG_FILE)
        except OSError:
            pass
    if os.path.exists(AUDIT_LOG_FILE):
        try:
            os.remove(AUDIT_LOG_FILE)
        except OSError:
            pass

    error_logger = logging.getLogger('error_logger')
    error_logger.setLevel(logging.ERROR)
    if not error_logger.handlers:
        efh = logging.FileHandler(LOG_FILE, 'w', 'utf-8')
        efh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        error_logger.addHandler(efh)

    audit_logger = logging.getLogger('audit_logger')
    audit_logger.setLevel(logging.INFO)
    if not audit_logger.handlers:
        afh = logging.FileHandler(AUDIT_LOG_FILE, 'w', 'utf-8')
        afh.setFormatter(logging.Formatter('%(asctime)s - INFO - %(message)s'))
        audit_logger.addHandler(afh)
    
    return error_logger, audit_logger

def register_xml_namespaces():
    """注册全局 XML 命名空间。"""
    for pfx, uri in NAMESPACES.items():
        ET.register_namespace(pfx, uri)

# 初始化
error_logger, audit_logger = setup_logging()
register_xml_namespaces()
