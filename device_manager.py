#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设备管理模块"""

import json
import os
import uuid
from datetime import datetime

DEVICES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'devices.json')


class DeviceManager:
    """设备管理器"""

    def __init__(self):
        self.devices = []
        self.load_devices()

    def load_devices(self):
        """加载设备列表"""
        try:
            if os.path.exists(DEVICES_FILE):
                with open(DEVICES_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.devices = data.get('devices', [])
        except Exception as e:
            print(f"加载设备列表失败: {e}")
            self.devices = []

    def save_devices(self):
        """保存设备列表"""
        try:
            data = {
                'devices': self.devices,
                'updated_at': datetime.now().isoformat()
            }
            with open(DEVICES_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"保存设备列表失败: {e}")
            return False

    def add_device(self, name, host, port=22, username='', password='', protocol='ssh', group='', description=''):
        """添加设备"""
        device = {
            'id': str(uuid.uuid4()),
            'name': name.strip(),
            'host': host.strip(),
            'port': int(port),
            'username': username.strip(),
            'password': password,  # 存储加密后的密码
            'protocol': protocol.lower(),
            'group': group.strip(),
            'description': description.strip(),
            'created_at': datetime.now().isoformat(),
            'last_connected': None
        }
        self.devices.append(device)
        return self.save_devices(), device['id']

    def update_device(self, device_id, **kwargs):
        """更新设备"""
        for device in self.devices:
            if device['id'] == device_id:
                for key, value in kwargs.items():
                    if key in ['name', 'host', 'username', 'password', 'group', 'description']:
                        device[key] = value.strip() if isinstance(value, str) else value
                    elif key == 'port':
                        device[key] = int(value)
                    elif key == 'protocol':
                        device[key] = value.lower()
                device['updated_at'] = datetime.now().isoformat()
                return self.save_devices()
        return False

    def delete_device(self, device_id):
        """删除设备"""
        self.devices = [d for d in self.devices if d['id'] != device_id]
        return self.save_devices()

    def get_device(self, device_id):
        """获取单个设备"""
        for device in self.devices:
            if device['id'] == device_id:
                return device
        return None

    def list_devices(self, group=None):
        """列出设备"""
        if group:
            return [d for d in self.devices if d['group'] == group]
        return self.devices

    def get_groups(self):
        """获取所有分组"""
        groups = list(set(d['group'] for d in self.devices if d['group']))
        groups.sort()
        return groups

    def update_last_connected(self, device_id):
        """更新最后连接时间"""
        for device in self.devices:
            if device['id'] == device_id:
                device['last_connected'] = datetime.now().isoformat()
                self.save_devices()
                break
