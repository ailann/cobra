# -*- coding: utf-8 -*-

"""
    api
    ~~~

    Implements API Server and Interface

    :author:    Feei <feei@feei.cn>
    :homepage:  https://github.com/wufeifei/cobra
    :license:   MIT, see LICENSE for more details.
    :copyright: Copyright (c) 2017 Feei. All rights reserved
"""
import errno
import json
import multiprocessing
import os
import re
import socket
import subprocess
import threading
import time
import traceback

import requests
from flask import Flask, request, render_template
from flask_restful import Api, Resource

from . import cli
from .cli import get_sid
from .config import Config, running_path, code_path, package_path
from .engine import Running
from .log import logger
from .utils import allowed_file, secure_filename, PY2

try:
    # Python 3
    import queue
except ImportError:
    # Python 2
    import Queue as queue

q = queue.Queue()
app = Flask(__name__, static_folder='templates/asset')


def producer(task):
    q.put(task)


def consumer():
    while True:
        task = q.get()
        p = multiprocessing.Process(target=cli.start, args=task)
        p.start()
        p.join()
        q.task_done()


class AddJob(Resource):
    @staticmethod
    def post():
        data = request.json
        if not data or data == "":
            return {"code": 1003, "msg": "Only support json, please post json data."}

        target = data.get("target")
        formatter = data.get("formatter")
        output = data.get("output")
        rule = data.get("rule")

        is_valid_key = key_verify(data=data)

        if is_valid_key is not True:
            return is_valid_key

        if not target or target == "":
            return {"code": 1002, "msg": "URL cannot be empty."}

        if not formatter or formatter == '':
            formatter = 'json'
        if not output or output == '':
            output = ''
        if not rule or rule == '':
            rule = ''

        # Report All Id
        a_sid = get_sid(target, True)
        running = Running(a_sid)

        # Write a_sid running data
        running.init_list(data=target)

        # Write a_sid running status
        data = {
            'status': 'running',
            'report': ''
        }
        running.status(data)

        if isinstance(target, list):
            for t in target:
                # Scan
                arg = (t, formatter, output, rule, a_sid)
                producer(task=arg)

            result = {
                'msg': 'Add scan job successfully.',
                'sid': a_sid,
                'total_target_num': len(target),
            }
        else:
            arg = (target, formatter, output, rule, a_sid)
            producer(task=arg)
            result = {
                'msg': 'Add scan job successfully.',
                'sid': a_sid,
                'total_target_num': 1,
            }

        return {"code": 1001, "result": result}


class JobStatus(Resource):
    @staticmethod
    def post():
        data = request.json
        if not data or data == "":
            return {"code": 1003, "msg": "Only support json, please post json data."}

        sid = data.get("sid")

        is_valid_key = key_verify(data=data)
        if is_valid_key is not True:
            return is_valid_key

        if not sid or sid == "":
            return {"code": 1002, "msg": "sid is required."}

        sid = str(data.get("sid"))  # 需要拼接入路径，转为字符串
        running = Running(sid)
        if running.is_file() is not True:
            data = {
                'code': 1004,
                'msg': 'scan id does not exist!',
                'sid': sid,
                'status': 'no such scan',
                'report': ''
            }
            return data
        else:
            result = running.status()
            r_data = running.list()
            if result['status'] == 'running':
                ret = True
                result['still_running'] = dict()
                for s_sid, git in r_data['sids'].items():
                    if Running(s_sid).is_file(True) is False:
                        result['still_running'].update({s_sid: git})
                        ret = False
                if ret:
                    result['status'] = 'done'
                    running.status(result)
            data = {
                'msg': 'success',
                'sid': sid,
                'status': result.get('status'),
                'report': result.get('report'),
                'still_running': result.get('still_running'),
                'total_target_num': r_data.get('total_target_num'),
                'not_finished': int(r_data.get('total_target_num')) - len(r_data.get('sids'))
                                + len(result.get('still_running')),
            }
        return {"code": 1001, "result": data}


class FileUpload(Resource):
    @staticmethod
    def post():
        """
        Scan by uploading compressed files
        :return:
        """
        if 'file' not in request.files:
            return {'code': 1002, 'result': "File can't empty!"}
        file_instance = request.files['file']
        if file_instance.filename == '':
            return {'code': 1002, 'result': "File name can't empty!"}
        if file_instance and allowed_file(file_instance.filename):
            filename = secure_filename(file_instance.filename)
            dst_directory = os.path.join(package_path, filename)
            file_instance.save(dst_directory)
            # Start scan
            a_sid = get_sid(dst_directory, True)
            data = {
                'status': 'running',
                'report': ''
            }
            Running(a_sid).status(data)
            try:
                cli.start(dst_directory, None, 'stream', None, a_sid=a_sid)
            except Exception as e:
                traceback.print_exc()
            code, result = 1001, {'sid': a_sid}
            return {'code': code, 'result': result}
        else:
            return {'code': 1002, 'msg': "This extension can't support!"}


class ResultData(Resource):
    @staticmethod
    def post():
        """
        pull scan result data.
        :return:
        """
        data = request.json
        if not data or data == "":
            return {"code": 1003, "msg": "Only support json, please post json data."}

        s_sid = data.get('sid')
        if not s_sid or s_sid == "":
            return {"code": 1002, "msg": "sid is required."}

        s_sid_file = os.path.join(running_path, '{sid}_data'.format(sid=s_sid))
        if not os.path.exists(s_sid_file):
            return {'code': 1002, 'msg': 'No such target.'}

        with open(s_sid_file, 'r') as f:
            scan_data = json.load(f)
            if scan_data.get('code') == 1001:
                scan_data = scan_data.get('result')
            else:
                return {
                    'code': scan_data.get('code'),
                    'msg': scan_data.get('msg'),
                }

        rule_filter = dict()
        for vul in scan_data.get('vulnerabilities'):
            rule_filter[vul.get('id')] = vul.get('rule_name')

        return {
            'code': 1001,
            'result': {
                'scan_data': scan_data,
                'rule_filter': rule_filter,
            }
        }


class ResultDetail(Resource):
    @staticmethod
    def post():
        """
        get vulnerable file content
        :return:
        """
        data = request.json
        if not data or data == "":
            return {'code': 1003, 'msg': 'Only support json, please post json data.'}

        target = data.get('target')
        file_path = data.get('file_path')

        if target.startswith('http'):
            target = re.findall(r'(.*?\.git)', target)[0]
            repo_user = target.split('/')[-2]
            repo_name = target.split('/')[-1].replace('.git', '')
            # repo_directory = os.path.join(os.path.join(os.path.join(code_path, 'git'), repo_user), repo_name)
            repo_directory = os.path.join(code_path, 'git', repo_user, repo_name)

            if PY2:
                file_path = map(secure_filename, [path.decode('utf-8') for path in file_path.split('/')])
            else:
                file_path = map(secure_filename, [path for path in file_path.split('/')])


            # 循环生成路径，避免文件越级读取
            file_name = repo_directory
            for _dir in file_path:
                file_name = os.path.join(file_name, _dir)
            if os.path.exists(file_name):
                extension = guess_type(file_name)
                if is_text(file_name):
                    with open(file_name, 'r') as f:
                        file_content = f.read()
                else:
                    file_content = 'This is a binary file.'
            else:
                return {'code': 1002, 'msg': 'No such file.'}

            return {'code': 1001, 'result': {'file_content': file_content,
                                             'extension': extension}}


@app.route('/', methods=['GET', 'POST'])
def summary():
    a_sid = request.args.get(key='sid')
    key = Config(level1="cobra", level2="secret_key").value
    if a_sid is None:
        return render_template(template_name_or_list='index.html',
                               key=key)

    status_url = request.url_root + 'api/status'
    post_data = {
        'key': key,
        'sid': a_sid,
    }
    headers = {
        "Content-Type": "application/json",
    }
    r = requests.post(url=status_url, headers=headers, data=json.dumps(post_data))
    try:
        scan_status = json.loads(r.text)
    except ValueError as e:
        return render_template(template_name_or_list='error.html',
                               msg='Check scan status failed: {0}'.format(e))

    if scan_status.get('code') != 1001:
        return render_template(template_name_or_list='error.html',
                               msg=scan_status.get('msg'))
    else:
        if scan_status.get('result').get('status') == 'running':
            return render_template(template_name_or_list='error.html',
                                   msg='Scan job is still running.',
                                   running=scan_status.get('result').get('still_running'))

        elif scan_status.get('result').get('status') == 'done':
            scan_status_file = os.path.join(running_path, '{sid}_status'.format(sid=a_sid))

            scan_list = Running(a_sid).list().get('sids')

            start_time = os.path.getctime(filename=scan_status_file)
            start_time = time.localtime(start_time)
            start_time = time.strftime('%Y-%m-%d %H:%M:%S', start_time)

            total_targets_number = len(scan_list)
            total_vul_number, critical_vul_number, high_vul_number, medium_vul_number, low_vul_number = 0, 0, 0, 0, 0
            rule_filter = dict()
            targets = list()

            for s_sid, target_str in scan_list.items():
                target_info = dict()

                # 分割项目地址与分支，默认 master
                split_target = target_str.split(':')
                if len(split_target) == 3:
                    target, branch = '{p}:{u}'.format(p=split_target[0], u=split_target[1]), split_target[-1]
                elif len(split_target) == 2:
                    target, branch = target_str, 'master'
                else:
                    logger.critical('Target url exception: {u}'.format(u=target_str))
                    target, branch = target_str, 'master'

                target_info.update({
                    'sid': s_sid,
                    'target': target,
                    'branch': branch,
                })
                s_sid_file = os.path.join(running_path, '{sid}_data'.format(sid=s_sid))
                with open(s_sid_file, 'r') as f:
                    s_sid_data = json.load(f)
                    if s_sid_data.get('code') != 1001:
                        continue
                    else:
                        s_sid_data = s_sid_data.get('result')
                total_vul_number += len(s_sid_data.get('vulnerabilities'))

                target_info.update({'total_vul_number': len(s_sid_data.get('vulnerabilities'))})
                target_info.update(s_sid_data)

                targets.append(target_info)

                for vul in s_sid_data.get('vulnerabilities'):
                    if 9 <= int(vul.get('level')) <= 10:
                        critical_vul_number += 1
                    elif 6 <= int(vul.get('level')) <= 8:
                        high_vul_number += 1
                    elif 3 <= int(vul.get('level')) <= 5:
                        medium_vul_number += 1
                    elif 1 <= int(vul.get('level')) <= 2:
                        low_vul_number += 1

                    try:
                        rule_filter[vul.get('rule_name')] += 1
                    except KeyError:
                        rule_filter[vul.get('rule_name')] = 1

            return render_template(template_name_or_list='summary.html',
                                   total_targets_number=total_targets_number,
                                   start_time=start_time,
                                   targets=targets,
                                   a_sid=a_sid,
                                   total_vul_number=total_vul_number,
                                   critical_vul_number=critical_vul_number,
                                   high_vul_number=high_vul_number,
                                   medium_vul_number=medium_vul_number,
                                   low_vul_number=low_vul_number,
                                   vuls=rule_filter, )


def key_verify(data):
    key = Config(level1="cobra", level2="secret_key").value
    _key = data.get("key")

    if _key == key:
        return True
    elif not _key or _key == "":
        return {"code": 1002, "msg": "Key cannot be empty."}
    elif not _key == key:
        return {"code": 4002, "msg": "Key verify failed."}
    else:
        return {"code": 4002, "msg": "Unknown key verify error."}


def is_text(fn):
    msg = subprocess.Popen(['file', fn], stdout=subprocess.PIPE).communicate()[0]
    return 'text' in msg.decode('utf-8')


def guess_type(fn):
    import mimetypes
    extension = mimetypes.guess_type(fn)[0]
    if extension:
        """text/x-python or text/x-java-source"""
        # extension = extension.split('/')[1]
        extension = extension.replace('-source', '')
    else:
        extension = fn.split('/')[-1].split('.')[-1]

    custom_ext = {
        'html': 'htmlmixed',
        'md': 'markdown',
    }
    if custom_ext.get(extension) is not None:
        extension = custom_ext.get(extension)

    return extension.lower()


def start(host, port, debug):
    logger.info('Start {host}:{port}'.format(host=host, port=port))
    api = Api(app)

    api.add_resource(AddJob, '/api/add')
    api.add_resource(JobStatus, '/api/status')
    api.add_resource(FileUpload, '/api/upload')
    api.add_resource(ResultData, '/api/list')
    api.add_resource(ResultDetail, '/api/detail')

    # consumer
    threads = []
    for i in range(5):
        threads.append(threading.Thread(target=consumer, args=()))

    for i in threads:
        i.setDaemon(daemonic=True)
        i.start()

    try:
        app.run(debug=debug, host=host, port=int(port), threaded=True, processes=1)
    except socket.error as v:
        if v.errno == errno.EACCES:
            logger.critical('[{err}] must root permission for start API Server!'.format(err=v.strerror))
            exit()
        else:
            logger.critical('{msg}'.format(msg=v.strerror))

    logger.info('API Server start success')
