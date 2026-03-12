#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文件管理模块 - 支持远程实例文件管理"""

import os
import base64
import json
import logging
import shutil
import stat
from datetime import datetime
from flask import request, jsonify, send_file
from io import BytesIO

logger = logging.getLogger(__name__)

# 文件管理会话存储（连接到终端会话）
file_sessions = {}


class FileManager:
    """文件管理器"""

    def __init__(self, base_path='/'):
        self.base_path = base_path

    def _safe_path(self, path):
        """安全路径处理，防止目录穿越"""
        # 规范化路径
        path = os.path.normpath(path)
        if path.startswith('..'):
            path = '/' + path
        # 确保以/开头
        if not path.startswith('/'):
            path = '/' + path
        return path

    def list_dir(self, path='/'):
        """列出目录内容"""
        path = self._safe_path(path)
        result = {'path': path, 'items': [], 'success': True}

        try:
            if not os.path.exists(path):
                result['success'] = False
                result['error'] = '目录不存在'
                return result

            if not os.path.isdir(path):
                result['success'] = False
                result['error'] = '不是目录'
                return result

            items = []
            for name in os.listdir(path):
                full_path = os.path.join(path, name)
                try:
                    stat_info = os.stat(full_path)
                    is_dir = os.path.isdir(full_path)
                    items.append({
                        'name': name,
                        'path': full_path,
                        'is_dir': is_dir,
                        'size': stat_info.st_size if not is_dir else 0,
                        'modified': datetime.fromtimestamp(stat_info.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                        'permissions': oct(stat_info.st_mode)[-3:],
                    })
                except Exception as e:
                    logger.debug(f"获取文件信息失败 {full_path}: {e}")

            # 排序：目录优先，然后按名称
            items.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
            result['items'] = items

        except PermissionError:
            result['success'] = False
            result['error'] = '权限不足'
        except Exception as e:
            result['success'] = False
            result['error'] = str(e)

        return result

    def read_file(self, path):
        """读取文件内容"""
        path = self._safe_path(path)
        result = {'path': path, 'success': True}

        try:
            if not os.path.exists(path):
                result['success'] = False
                result['error'] = '文件不存在'
                return result

            if os.path.isdir(path):
                result['success'] = False
                result['error'] = '不能读取目录'
                return result

            # 检查文件大小
            file_size = os.path.getsize(path)
            if file_size > 10 * 1024 * 1024:  # 10MB限制
                result['success'] = False
                result['error'] = '文件过大，请使用下载功能'
                return result

            # 尝试以文本方式读取
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    result['content'] = f.read()
                    result['encoding'] = 'utf-8'
                    result['size'] = file_size
            except UnicodeDecodeError:
                # 二进制文件，返回base64
                with open(path, 'rb') as f:
                    result['content'] = base64.b64encode(f.read()).decode('ascii')
                    result['encoding'] = 'base64'
                    result['size'] = file_size
                    result['is_binary'] = True

        except PermissionError:
            result['success'] = False
            result['error'] = '权限不足'
        except Exception as e:
            result['success'] = False
            result['error'] = str(e)

        return result

    def write_file(self, path, content, encoding='utf-8'):
        """写入文件内容"""
        path = self._safe_path(path)
        result = {'path': path, 'success': True}

        try:
            # 确保目录存在
            dir_path = os.path.dirname(path)
            if dir_path and not os.path.exists(dir_path):
                os.makedirs(dir_path, exist_ok=True)

            if encoding == 'base64':
                content = base64.b64decode(content)
                with open(path, 'wb') as f:
                    f.write(content)
            else:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(content)

            result['size'] = len(content)

        except PermissionError:
            result['success'] = False
            result['error'] = '权限不足'
        except Exception as e:
            result['success'] = False
            result['error'] = str(e)

        return result

    def delete(self, path):
        """删除文件或目录"""
        path = self._safe_path(path)
        result = {'path': path, 'success': True}

        try:
            if not os.path.exists(path):
                result['success'] = False
                result['error'] = '文件或目录不存在'
                return result

            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)

        except PermissionError:
            result['success'] = False
            result['error'] = '权限不足'
        except Exception as e:
            result['success'] = False
            result['error'] = str(e)

        return result

    def mkdir(self, path):
        """创建目录"""
        path = self._safe_path(path)
        result = {'path': path, 'success': True}

        try:
            os.makedirs(path, exist_ok=True)
        except PermissionError:
            result['success'] = False
            result['error'] = '权限不足'
        except Exception as e:
            result['success'] = False
            result['error'] = str(e)

        return result

    def rename(self, old_path, new_name):
        """重命名文件或目录"""
        old_path = self._safe_path(old_path)
        result = {'success': True}

        try:
            if not os.path.exists(old_path):
                result['success'] = False
                result['error'] = '文件或目录不存在'
                return result

            new_path = os.path.join(os.path.dirname(old_path), new_name)
            os.rename(old_path, new_path)
            result['new_path'] = new_path

        except PermissionError:
            result['success'] = False
            result['error'] = '权限不足'
        except Exception as e:
            result['success'] = False
            result['error'] = str(e)

        return result

    def download(self, path):
        """下载文件"""
        path = self._safe_path(path)

        if not os.path.exists(path):
            return None, '文件不存在'

        if os.path.isdir(path):
            # 打包目录
            import tarfile
            buffer = BytesIO()
            with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
                tar.add(path, arcname=os.path.basename(path))
            buffer.seek(0)
            return buffer, os.path.basename(path) + '.tar.gz'

        return path, os.path.basename(path)

    def upload(self, path, file_data, filename):
        """上传文件"""
        path = self._safe_path(path)
        result = {'success': True}

        try:
            # 如果path是目录，保存在该目录下
            if os.path.isdir(path):
                full_path = os.path.join(path, filename)
            else:
                full_path = path

            # 确保目录存在
            dir_path = os.path.dirname(full_path)
            if dir_path and not os.path.exists(dir_path):
                os.makedirs(dir_path, exist_ok=True)

            with open(full_path, 'wb') as f:
                f.write(file_data)

            result['path'] = full_path
            result['size'] = len(file_data)

        except PermissionError:
            result['success'] = False
            result['error'] = '权限不足'
        except Exception as e:
            result['success'] = False
            result['error'] = str(e)

        return result

    def get_stat(self, path):
        """获取文件/目录详细信息"""
        path = self._safe_path(path)
        result = {'path': path, 'success': True}

        try:
            if not os.path.exists(path):
                result['success'] = False
                result['error'] = '文件或目录不存在'
                return result

            stat_info = os.stat(path)
            result.update({
                'name': os.path.basename(path),
                'is_dir': os.path.isdir(path),
                'size': stat_info.st_size,
                'modified': datetime.fromtimestamp(stat_info.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                'accessed': datetime.fromtimestamp(stat_info.st_atime).strftime('%Y-%m-%d %H:%M:%S'),
                'created': datetime.fromtimestamp(stat_info.st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                'permissions': oct(stat_info.st_mode)[-3:],
                'uid': stat_info.st_uid,
                'gid': stat_info.st_gid,
            })

        except Exception as e:
            result['success'] = False
            result['error'] = str(e)

        return result

    def search(self, path, pattern):
        """搜索文件"""
        path = self._safe_path(path)
        result = {'path': path, 'pattern': pattern, 'items': [], 'success': True}

        try:
            import fnmatch
            for root, dirs, files in os.walk(path):
                for name in files + dirs:
                    if fnmatch.fnmatch(name.lower(), f'*{pattern.lower()}*'):
                        full_path = os.path.join(root, name)
                        try:
                            stat_info = os.stat(full_path)
                            result['items'].append({
                                'name': name,
                                'path': full_path,
                                'is_dir': os.path.isdir(full_path),
                                'size': stat_info.st_size if not os.path.isdir(full_path) else 0,
                                'modified': datetime.fromtimestamp(stat_info.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                            })
                        except:
                            pass
                # 限制搜索结果数量
                if len(result['items']) > 100:
                    break

        except Exception as e:
            result['success'] = False
            result['error'] = str(e)

        return result
