#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网络设备管理平台 - 主应用"""

# 必须在所有import之前进行monkey patch
import eventlet
eventlet.monkey_patch(all=True)

import os
import sys
import json
import pty
import subprocess
import signal
import struct
import fcntl
import termios
import logging
import hashlib
import secrets
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, send_file
from flask_socketio import SocketIO, emit
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from io import BytesIO

from ai_module import AIManager
from device_manager import DeviceManager
from file_manager import FileManager

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', 'app.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Flask应用
app = Flask(__name__)
app.config['SECRET_KEY'] = secrets.token_hex(32)
app.config['SESSION_TYPE'] = 'filesystem'

socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*", ping_timeout=120, ping_interval=25)

# 登录管理
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = '请先登录'

# AI管理器
ai_manager = AIManager()

# 设备管理器
device_manager = DeviceManager()

# 用户配置文件路径
USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'users.json')

# 终端会话存储
terminal_sessions = {}


def load_users():
    """加载用户配置"""
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    # 默认管理员账号
    default_users = {
        "admin": {
            "password_hash": hashlib.sha256("admin123".encode()).hexdigest(),
            "role": "admin",
            "display_name": "管理员"
        }
    }
    save_users(default_users)
    return default_users


def save_users(users):
    """保存用户配置"""
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


class User(UserMixin):
    def __init__(self, username, role='user', display_name=''):
        self.id = username
        self.username = username
        self.role = role
        self.display_name = display_name or username


@login_manager.user_loader
def load_user(username):
    users = load_users()
    if username in users:
        u = users[username]
        return User(username, u.get('role', 'user'), u.get('display_name', username))
    return None


# ========== 路由 ==========

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('terminal'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('terminal'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        users = load_users()

        if username in users:
            pwd_hash = hashlib.sha256(password.encode()).hexdigest()
            if users[username]['password_hash'] == pwd_hash:
                user = User(username, users[username].get('role', 'user'),
                            users[username].get('display_name', username))
                login_user(user, remember=True)
                logger.info(f"用户登录成功: {username}")
                next_page = request.args.get('next')
                return redirect(next_page or url_for('terminal'))

        flash('用户名或密码错误', 'error')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logger.info(f"用户登出: {current_user.username}")
    logout_user()
    return redirect(url_for('login'))


@app.route('/terminal')
@login_required
def terminal():
    devices = device_manager.list_devices()
    groups = device_manager.get_groups()
    return render_template('terminal.html', user=current_user, ai_available=ai_manager.is_available(),
                         devices=devices, groups=groups)


@app.route('/devices')
@login_required
def devices_page():
    return render_template('devices.html', user=current_user)


@app.route('/api/devices', methods=['GET'])
@login_required
def api_get_devices():
    group = request.args.get('group', '')
    devices = device_manager.list_devices(group=group if group else None)
    return jsonify({'devices': devices, 'groups': device_manager.get_groups()})


@app.route('/api/devices', methods=['POST'])
@login_required
def api_add_device():
    data = request.get_json()
    required = ['name', 'host']
    for field in required:
        if not data.get(field):
            return jsonify({'error': f'缺少必需字段: {field}'}), 400

    success, device_id = device_manager.add_device(
        name=data['name'],
        host=data['host'],
        port=data.get('port', 22),
        username=data.get('username', ''),
        password=data.get('password', ''),
        protocol=data.get('protocol', 'ssh'),
        group=data.get('group', ''),
        description=data.get('description', '')
    )

    if success:
        return jsonify({'message': '设备添加成功', 'device_id': device_id})
    else:
        return jsonify({'error': '设备添加失败'}), 500


@app.route('/api/devices/<device_id>', methods=['PUT'])
@login_required
def api_update_device(device_id):
    data = request.get_json()
    if device_manager.update_device(device_id, **data):
        return jsonify({'message': '设备更新成功'})
    else:
        return jsonify({'error': '设备更新失败'}), 404


@app.route('/api/devices/<device_id>', methods=['DELETE'])
@login_required
def api_delete_device(device_id):
    if device_manager.delete_device(device_id):
        return jsonify({'message': '设备删除成功'})
    else:
        return jsonify({'error': '设备删除失败'}), 404


@app.route('/api/devices/<device_id>', methods=['GET'])
@login_required
def api_get_device(device_id):
    device = device_manager.get_device(device_id)
    if device:
        return jsonify(device)
    else:
        return jsonify({'error': '设备不存在'}), 404


@app.route('/ai/config', methods=['GET'])
@login_required
def ai_config_page():
    if current_user.role != 'admin':
        flash('无权限访问', 'error')
        return redirect(url_for('terminal'))
    return render_template('ai_config.html', user=current_user, config=ai_manager.get_config())


@app.route('/api/ai/config', methods=['GET'])
@login_required
def get_ai_config():
    if current_user.role != 'admin':
        return jsonify({"error": "无权限"}), 403
    return jsonify(ai_manager.get_config())


@app.route('/api/ai/config', methods=['POST'])
@login_required
def save_ai_config():
    if current_user.role != 'admin':
        return jsonify({"error": "无权限"}), 403

    data = request.get_json()
    if not data:
        return jsonify({"error": "无效的配置数据"}), 400

    # 如果密钥是掩码形式，保留原密钥
    old_config = ai_manager.get_full_config()
    for pname, pconf in data.get('providers', {}).items():
        if 'api_key' in pconf and '****' in pconf.get('api_key', ''):
            old_key = old_config.get('providers', {}).get(pname, {}).get('api_key', '')
            pconf['api_key'] = old_key

    if ai_manager.save_config(data):
        return jsonify({"message": "配置已保存", "ai_available": ai_manager.is_available()})
    return jsonify({"error": "保存配置失败"}), 500


@app.route('/api/ai/test', methods=['POST'])
@login_required
def test_ai():
    if not ai_manager.is_available():
        return jsonify({"error": "AI服务未配置"}), 400
    result = ai_manager.chat("请简短回复：你好，连接测试成功。")
    return jsonify(result)


@app.route('/api/ai/chat', methods=['POST'])
@login_required
def ai_chat():
    if not ai_manager.is_available():
        return jsonify({"error": "AI服务未配置或未启用"}), 400

    data = request.get_json()
    message = data.get('message', '')
    context = data.get('context', '')

    if not message:
        return jsonify({"error": "消息不能为空"}), 400

    result = ai_manager.chat(message, context=context)
    return jsonify(result)


@app.route('/api/ai/analyze_error', methods=['POST'])
@login_required
def ai_analyze_error():
    if not ai_manager.is_available():
        return jsonify({"error": "AI服务未配置或未启用"}), 400

    data = request.get_json()
    error_output = data.get('error_output', '')

    if not error_output:
        return jsonify({"error": "错误输出不能为空"}), 400

    result = ai_manager.analyze_error(error_output)
    return jsonify(result)


@app.route('/api/user/change_password', methods=['POST'])
@login_required
def change_password():
    data = request.get_json()
    old_pwd = data.get('old_password', '')
    new_pwd = data.get('new_password', '')

    if not old_pwd or not new_pwd:
        return jsonify({"error": "密码不能为空"}), 400

    if len(new_pwd) < 6:
        return jsonify({"error": "新密码至少6个字符"}), 400

    users = load_users()
    old_hash = hashlib.sha256(old_pwd.encode()).hexdigest()

    if users.get(current_user.username, {}).get('password_hash') != old_hash:
        return jsonify({"error": "原密码错误"}), 400

    users[current_user.username]['password_hash'] = hashlib.sha256(new_pwd.encode()).hexdigest()
    save_users(users)
    return jsonify({"message": "密码已修改"})


# ========== 文件管理 ==========

@app.route('/files')
@login_required
def files_page():
    return render_template('files.html', user=current_user)


@app.route('/api/files/list', methods=['GET'])
@login_required
def api_files_list():
    path = request.args.get('path', '/')
    fm = FileManager()
    return jsonify(fm.list_dir(path))


@app.route('/api/files/read', methods=['GET'])
@login_required
def api_files_read():
    path = request.args.get('path', '/')
    fm = FileManager()
    return jsonify(fm.read_file(path))


@app.route('/api/files/write', methods=['POST'])
@login_required
def api_files_write():
    data = request.get_json()
    path = data.get('path', '')
    content = data.get('content', '')
    encoding = data.get('encoding', 'utf-8')
    fm = FileManager()
    return jsonify(fm.write_file(path, content, encoding))


@app.route('/api/files/delete', methods=['POST'])
@login_required
def api_files_delete():
    data = request.get_json()
    path = data.get('path', '')
    fm = FileManager()
    return jsonify(fm.delete(path))


@app.route('/api/files/mkdir', methods=['POST'])
@login_required
def api_files_mkdir():
    data = request.get_json()
    path = data.get('path', '')
    fm = FileManager()
    return jsonify(fm.mkdir(path))


@app.route('/api/files/rename', methods=['POST'])
@login_required
def api_files_rename():
    data = request.get_json()
    old_path = data.get('old_path', '')
    new_name = data.get('new_name', '')
    fm = FileManager()
    return jsonify(fm.rename(old_path, new_name))


@app.route('/api/files/upload', methods=['POST'])
@login_required
def api_files_upload():
    path = request.form.get('path', '/')
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': '没有文件'})
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': '没有选择文件'})
    fm = FileManager()
    return jsonify(fm.upload(path, file.read(), file.filename))


@app.route('/api/files/download', methods=['GET'])
@login_required
def api_files_download():
    path = request.args.get('path', '/')
    fm = FileManager()
    file_data, filename = fm.download(path)
    if file_data is None:
        return jsonify({'error': filename}), 404

    if isinstance(file_data, BytesIO):
        return send_file(file_data, as_attachment=True, download_name=filename)
    else:
        return send_file(file_data, as_attachment=True, download_name=filename)


@app.route('/api/files/stat', methods=['GET'])
@login_required
def api_files_stat():
    path = request.args.get('path', '/')
    fm = FileManager()
    return jsonify(fm.get_stat(path))


@app.route('/api/files/search', methods=['GET'])
@login_required
def api_files_search():
    path = request.args.get('path', '/')
    pattern = request.args.get('pattern', '')
    fm = FileManager()
    return jsonify(fm.search(path, pattern))


# ========== AI Agent ==========

@app.route('/api/ai/agent', methods=['POST'])
@login_required
def ai_agent():
    """AI Agent模式 - 自然语言执行运维任务"""
    if not ai_manager.is_available():
        return jsonify({"error": "AI服务未配置或未启用"}), 400

    data = request.get_json()
    message = data.get('message', '')
    context = data.get('context', '')
    history = data.get('history', [])  # 对话历史

    if not message:
        return jsonify({"error": "消息不能为空"}), 400

    # AI Agent系统提示词
    agent_prompt = """你是一个智能运维Agent。你的任务是：
1. 理解用户的自然语言需求
2. 规划执行步骤
3. 生成可执行的命令

规则：
- 每个命令用单独的代码块包裹，使用 ```bash 标记
- 对于危险操作（删除、格式化、修改系统配置等），必须先警告用户确认
- 提供清晰的执行步骤说明
- 如果需要多个命令，按顺序列出
- 根据对话历史理解上下文，支持多轮对话

输出格式示例：
**分析**：简要分析用户需求

**执行步骤**：
1. 步骤说明
2. 步骤说明

**命令**：
```bash
命令内容
```

**说明**：执行后的预期结果"""

    messages = [
        {"role": "system", "content": agent_prompt},
    ]

    # 添加系统上下文
    if context:
        messages.append({
            "role": "system",
            "content": f"当前终端输出上下文：\n{context}"
        })

    # 添加对话历史
    for h in history:
        if h.get('role') and h.get('content'):
            messages.append({"role": h["role"], "content": h["content"]})

    # 添加当前消息
    messages.append({"role": "user", "content": message})

    try:
        response = ai_manager.provider.chat(messages)
        return jsonify({"response": response, "success": True})
    except Exception as e:
        return jsonify({"error": f"AI调用失败: {str(e)}"})


# ========== WebSocket终端 ==========

def set_terminal_size(fd, rows, cols):
    """设置终端大小"""
    size = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, size)


def cleanup_session(sid):
    """清理终端会话"""
    if sid in terminal_sessions:
        sess = terminal_sessions[sid]
        try:
            pid = sess.get('pid')
            fd = sess.get('fd')
            if fd:
                os.close(fd)
            if pid:
                os.kill(pid, signal.SIGTERM)
                try:
                    os.waitpid(pid, os.WNOHANG)
                except:
                    pass
        except Exception as e:
            logger.debug(f"清理会话异常: {e}")
        del terminal_sessions[sid]
        logger.info(f"终端会话已清理: {sid}")


@socketio.on('connect')
def handle_connect():
    logger.info(f"WebSocket连接: {request.sid}")


@socketio.on('disconnect')
def handle_disconnect():
    cleanup_session(request.sid)
    logger.info(f"WebSocket断开: {request.sid}")


@socketio.on('terminal_connect')
def handle_terminal_connect(data=None):
    """创建终端会话"""
    sid = request.sid
    cleanup_session(sid)

    conn_type = 'local'
    host = ''
    port = 22
    username = ''
    password = ''
    device_id = ''

    if data:
        conn_type = data.get('type', 'local')
        host = data.get('host', '')
        port = int(data.get('port', 22))
        username = data.get('username', '')
        password = data.get('password', '')
        device_id = data.get('device_id', '')  # 新增设备ID参数

    # 如果提供了设备ID，则从设备列表获取连接信息
    if device_id:
        device = device_manager.get_device(device_id)
        if device:
            conn_type = device['protocol']
            host = device['host']
            port = device['port']
            username = device['username']
            password = device['password']
            # 更新最后连接时间
            device_manager.update_last_connected(device_id)
            logger.info(f"通过设备连接: {device['name']} ({host}:{port})")

    if conn_type == 'ssh' and host:
        # SSH连接
        try:
            import paramiko
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(host, port=port, username=username, password=password, timeout=10)
            channel = client.invoke_shell(term='xterm-256color', width=120, height=40)
            channel.setblocking(0)

            terminal_sessions[sid] = {
                'type': 'ssh',
                'client': client,
                'channel': channel,
                'device_id': device_id
            }

            emit('terminal_output', {'data': f'\r\n已连接到 {host}:{port}\r\n'})
            logger.info(f"SSH连接成功: {host}:{port} (sid={sid})")

            # 启动SSH读取协程
            def ssh_read():
                while sid in terminal_sessions:
                    sess = terminal_sessions.get(sid)
                    if not sess or sess.get('type') != 'ssh':
                        break
                    ch = sess.get('channel')
                    if not ch:
                        break
                    try:
                        if ch.recv_ready():
                            data = ch.recv(4096)
                            if data:
                                socketio.emit('terminal_output', {'data': data.decode('utf-8', errors='replace')}, room=sid)
                        elif ch.closed:
                            socketio.emit('terminal_output', {'data': '\r\n[连接已断开]\r\n'}, room=sid)
                            break
                        else:
                            eventlet.sleep(0.05)
                    except Exception as e:
                        logger.error(f"SSH读取错误: {e}")
                        socketio.emit('terminal_output', {'data': f'\r\n[连接错误: {e}]\r\n'}, room=sid)
                        break
                cleanup_session(sid)

            eventlet.spawn(ssh_read)

        except Exception as e:
            emit('terminal_output', {'data': f'\r\n连接失败: {str(e)}\r\n'})
            logger.error(f"SSH连接失败: {e}")

    else:
        # 本地终端
        try:
            pid, fd = pty.fork()
            if pid == 0:
                # 子进程
                os.environ['TERM'] = 'xterm-256color'
                os.environ['LANG'] = 'en_US.UTF-8'
                os.execvp('/bin/bash', ['/bin/bash', '--login'])
            else:
                # 父进程
                set_terminal_size(fd, 40, 120)
                terminal_sessions[sid] = {
                    'type': 'local',
                    'pid': pid,
                    'fd': fd,
                    'device_id': device_id
                }
                emit('terminal_output', {'data': ''})
                logger.info(f"本地终端已创建: pid={pid} (sid={sid})")

                # 启动读取协程
                def pty_read():
                    while sid in terminal_sessions:
                        sess = terminal_sessions.get(sid)
                        if not sess or sess.get('type') != 'local':
                            break
                        fd = sess.get('fd')
                        if fd is None:
                            break
                        try:
                            # 使用eventlet的select
                            r, _, _ = eventlet.select.select([fd], [], [], 0.1)
                            if r:
                                data = os.read(fd, 4096)
                                if data:
                                    socketio.emit('terminal_output', {'data': data.decode('utf-8', errors='replace')}, room=sid)
                                else:
                                    break
                            else:
                                eventlet.sleep(0.01)
                        except (OSError, IOError):
                            break
                        except Exception as e:
                            logger.error(f"PTY读取错误: {e}")
                            break
                    if sid in terminal_sessions:
                        socketio.emit('terminal_output', {'data': '\r\n[会话已结束]\r\n'}, room=sid)
                        cleanup_session(sid)

                eventlet.spawn(pty_read)

        except Exception as e:
            emit('terminal_output', {'data': f'\r\n创建终端失败: {str(e)}\r\n'})
            logger.error(f"创建本地终端失败: {e}")


@socketio.on('terminal_input')
def handle_terminal_input(data):
    """处理终端输入"""
    sid = request.sid
    sess = terminal_sessions.get(sid)
    if not sess:
        return

    input_data = data.get('data', '')
    if not input_data:
        return

    try:
        if sess['type'] == 'local':
            os.write(sess['fd'], input_data.encode('utf-8'))
        elif sess['type'] == 'ssh':
            sess['channel'].send(input_data.encode('utf-8'))
    except Exception as e:
        logger.error(f"终端输入错误: {e}")
        emit('terminal_output', {'data': f'\r\n[输入错误: {e}]\r\n'})


@socketio.on('terminal_resize')
def handle_terminal_resize(data):
    """处理终端大小变化"""
    sid = request.sid
    sess = terminal_sessions.get(sid)
    if not sess:
        return

    rows = data.get('rows', 40)
    cols = data.get('cols', 120)

    try:
        if sess['type'] == 'local' and sess.get('fd'):
            set_terminal_size(sess['fd'], rows, cols)
        elif sess['type'] == 'ssh' and sess.get('channel'):
            sess['channel'].resize_pty(width=cols, height=rows)
    except Exception as e:
        logger.debug(f"终端resize错误: {e}")


@socketio.on('terminal_disconnect')
def handle_terminal_disconnect():
    """断开终端"""
    cleanup_session(request.sid)
    emit('terminal_output', {'data': '\r\n[已断开连接]\r\n'})


if __name__ == '__main__':
    logger.info("=" * 50)
    logger.info("网络设备管理平台启动")
    logger.info(f"默认账号: admin / admin123")
    logger.info("=" * 50)
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
