# utils/converters.py
import base64
import requests
import logging

logger = logging.getLogger(__name__)

def url_to_base64(url):
    """从 URL 下载文件并将其转换为 Base64 编码的字符串。"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        # Dify返回的媒体链接通常不需要特别大的超时，但30秒是合理的
        response = requests.get(url, timeout=30, headers=headers, stream=True) 
        response.raise_for_status()

        # 限制从URL下载并直接转Base64的大小，以防Dify返回超大文件链接
        max_size_for_url_b64 = 10 * 1024 * 1024 # 例如，限制为10MB
        content_length = response.headers.get('Content-Length')
        if content_length and int(content_length) > max_size_for_url_b64:
            logger.error(f"从URL {url} 下载的文件大小 ({content_length} bytes) 超过直接Base64转换限制 ({max_size_for_url_b64} bytes)。")
            return None

        content = b''
        bytes_read = 0
        for chunk in response.iter_content(chunk_size=8192): # Read in chunks
            if chunk: # filter out keep-alive new chunks
                content += chunk
                bytes_read += len(chunk)
                if bytes_read > max_size_for_url_b64:
                    logger.error(f"下载文件 {url} 时，读取字节 ({bytes_read}) 超过限制 ({max_size_for_url_b64} bytes)。")
                    # Clean up stream
                    response.close()
                    return None
        
        if not content:
            logger.warning(f"从 URL {url} 下载的内容为空。")
            return None
            
        return base64.b64encode(content).decode('utf-8')
    except requests.exceptions.RequestException as e:
        logger.error(f"从 URL 下载文件失败 {url}: {e}")
        return None
    except Exception as e:
        logger.error(f"转换 URL 内容到 Base64 时出错 {url}: {e}", exc_info=True)
        return None

def file_path_to_base64(file_path):
    """读取本地文件并将其转换为 Base64 编码的字符串。"""
    try:
        with open(file_path, 'rb') as f:
            return base64.b64encode(f.read()).decode('utf-8')
    except IOError as e:
        logger.error(f"读取本地文件失败 {file_path}: {e}")
        return None
    except Exception as e:
        logger.error(f"转换文件到 Base64 时出错 {file_path}: {e}", exc_info=True)
        return None
