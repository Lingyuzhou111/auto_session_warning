#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动掉线预警插件
实现微信session掉线预警功能的独立插件

功能：
1. 通过指令查询当前在线状态和预计掉线时间
2. 获取和管理预警配置信息
3. 启用/禁用自动掉线预警模式
4. 调整预警阈值
5. 手动进行掉线预警测试

作者: Assistant
版本: 1.0
"""

import os
import json
import time
import asyncio
import aiohttp
import threading
import base64
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import hashlib
import string
import random

import plugins
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from plugins import *


@plugins.register(
    name="auto_session_warning",
    desire_priority=99,
    hidden=False,
    desc="微信session自动掉线预警插件",
    version="1.0",
    author="Assistant",
    namecn="自动掉线预警"
)
class AutoSessionWarningPlugin(Plugin):
    
    def __init__(self):
        super().__init__()
        self.config = super().load_config()
        if not self.config:
            self.config = self._load_default_config()
            
        # 预警配置
        self.warning_enabled = self.config.get("auto_session_warning_enabled", True)
        self.warning_threshold = self.config.get("auto_session_warning_threshold", 2)
        self.warning_target = self.config.get("auto_session_warning_target", "")
        
        # API配置 - 使用与wx849_channel.py相同的默认值
        self.api_host = self.config.get("api_host", "127.0.0.1")
        self.api_port = self.config.get("api_port", 9000)  # 修正为与主通道一致的端口
        self.api_path_prefix = self.config.get("api_path_prefix", "/VXAPI")
        self.base_url = f"http://{self.api_host}:{self.api_port}{self.api_path_prefix}"
        
        # Session配置
        self.session_duration_hours = self.config.get("session_duration_hours", 72)
        self.check_interval_hours = self.config.get("check_interval_hours", 2)
        
        # 运行状态
        self.is_running = False
        self.background_thread = None
        self.last_warning_time = 0
        
        # 当前微信ID和设备ID（从wx849_device_info.json获取）
        self.current_wxid = ""
        self.current_device_id = ""
        
        # 注册事件处理器
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        
        # 启动后台检查（如果启用了预警功能）
        if self.warning_enabled:
            self._start_background_check()
            
        logger.info("[AutoSessionWarning] 插件初始化完成")
    
    def _load_default_config(self):
        """加载默认配置"""
        return {
            "auto_session_warning_enabled": True,
            "auto_session_warning_threshold": 2,
            "auto_session_warning_target": "",
            "api_host": "127.0.0.1",
            "api_port": 9000,  # 修正为与wx849_channel.py一致的端口
            "api_path_prefix": "/VXAPI",
            "session_duration_hours": 72,
            "check_interval_hours": 2
        }
    
    def on_handle_context(self, e_context: EventContext):
        """处理消息上下文"""
        if e_context["context"].type != ContextType.TEXT:
            return
        
        content = e_context["context"].content.strip()
        msg = e_context["context"]["msg"]
        
        # 检查是否是预警相关指令
        if content == "$预警状态":
            reply = self._handle_status_query()
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            
        elif content == "$预警配置":
            reply = self._handle_config_query()
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            
        elif content == "$预警启用":
            reply = self._handle_enable_warning()
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            
        elif content == "$预警禁用":
            reply = self._handle_disable_warning()
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            
        elif content.startswith("$预警阈值"):
            reply = self._handle_threshold_setting(content)
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            
        elif content == "$预警测试":
            reply = self._handle_warning_test(msg)
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            

    
    def _handle_status_query(self) -> Reply:
        """处理预警状态查询"""
        try:
            # 获取当前登录信息
            self._load_current_login_info()
            
            if not self.current_wxid:
                reply = Reply()
                reply.type = ReplyType.TEXT
                reply.content = "❌ 无法获取当前登录信息，请确保微信已正常登录。"
                return reply
            
            # 获取登录时间和计算在线时长
            login_time = self._get_real_login_time()
            if not login_time:
                reply = Reply()
                reply.type = ReplyType.TEXT
                reply.content = "❌ 无法获取登录时间信息。"
                return reply
            
            # 计算在线时长
            now = datetime.now()
            online_duration = now - login_time
            online_hours = online_duration.total_seconds() / 3600
            
            # 计算预计掉线时间
            remaining_hours = self.session_duration_hours - online_hours
            
            reply = Reply()
            reply.type = ReplyType.TEXT
            
            if remaining_hours > 0:
                reply.content = (
                    f"⚠️ 当前预警状态\n"
                    f"您已持续在线超过{online_hours:.1f}小时，"
                    f"预计{remaining_hours:.1f}小时内即将掉线。"
                )
            else:
                reply.content = (
                    f"🔴 当前预警状态\n"
                    f"您已持续在线超过{online_hours:.1f}小时，"
                    f"session可能已过期，建议立即重新登录！"
                )
            
            return reply
            
        except Exception as e:
            logger.error(f"[AutoSessionWarning] 处理状态查询失败: {e}")
            reply = Reply()
            reply.type = ReplyType.TEXT
            reply.content = f"❌ 查询预警状态失败: {str(e)}"
            return reply
    
    def _handle_config_query(self) -> Reply:
        """处理配置信息查询"""
        try:
            # 获取当前登录信息
            self._load_current_login_info()
            
            reply = Reply()
            reply.type = ReplyType.TEXT
            reply.content = (
                f"📋 配置信息:\n"
                f"   API服务器: {self.api_host}:{self.api_port}{self.api_path_prefix}\n"
                f"   登录微信ID: {self.current_wxid or '未获取到'}\n"
                f"   设备ID: {self.current_device_id or '未获取到'}\n"
                f"   预警接收者: {self.warning_target or '未设置'}\n"
                f"   预警状态: {'已启用' if self.warning_enabled else '已禁用'}\n"
                f"   预警阈值: {self.warning_threshold}小时"
            )
            
            return reply
            
        except Exception as e:
            logger.error(f"[AutoSessionWarning] 处理配置查询失败: {e}")
            reply = Reply()
            reply.type = ReplyType.TEXT
            reply.content = f"❌ 查询配置信息失败: {str(e)}"
            return reply
    
    def _handle_enable_warning(self) -> Reply:
        """处理启用预警"""
        try:
            self.warning_enabled = True
            self.config["auto_session_warning_enabled"] = True
            self.save_config(self.config)
            
            # 启动后台检查
            self._start_background_check()
            
            reply = Reply()
            reply.type = ReplyType.TEXT
            reply.content = "✅ 已启用自动掉线预警功能"
            
            return reply
            
        except Exception as e:
            logger.error(f"[AutoSessionWarning] 启用预警失败: {e}")
            reply = Reply()
            reply.type = ReplyType.TEXT
            reply.content = f"❌ 启用预警失败: {str(e)}"
            return reply
    
    def _handle_disable_warning(self) -> Reply:
        """处理禁用预警"""
        try:
            self.warning_enabled = False
            self.config["auto_session_warning_enabled"] = False
            self.save_config(self.config)
            
            # 停止后台检查
            self._stop_background_check()
            
            reply = Reply()
            reply.type = ReplyType.TEXT
            reply.content = "⛔️ 已禁用自动掉线预警功能"
            
            return reply
            
        except Exception as e:
            logger.error(f"[AutoSessionWarning] 禁用预警失败: {e}")
            reply = Reply()
            reply.type = ReplyType.TEXT
            reply.content = f"❌ 禁用预警失败: {str(e)}"
            return reply
    
    def _handle_threshold_setting(self, content: str) -> Reply:
        """处理阈值设置"""
        try:
            # 解析阈值参数
            parts = content.split()
            if len(parts) != 2:
                reply = Reply()
                reply.type = ReplyType.TEXT
                reply.content = "❌ 指令格式错误，请使用：$预警阈值 xh（如：$预警阈值 2h）"
                return reply
            
            threshold_str = parts[1].lower()
            if not threshold_str.endswith('h'):
                reply = Reply()
                reply.type = ReplyType.TEXT
                reply.content = "❌ 阈值格式错误，请使用小时单位，如：2h"
                return reply
            
            try:
                threshold = float(threshold_str[:-1])
            except ValueError:
                reply = Reply()
                reply.type = ReplyType.TEXT
                reply.content = "❌ 阈值必须是数字，如：2h"
                return reply
            
            if threshold < 0 or threshold > 72:
                reply = Reply()
                reply.type = ReplyType.TEXT
                reply.content = "❌ 阈值范围必须在0-72小时之间"
                return reply
            
            # 更新阈值
            self.warning_threshold = threshold
            self.config["auto_session_warning_threshold"] = threshold
            self.save_config(self.config)
            
            trigger_hours = 72 - threshold
            
            reply = Reply()
            reply.type = ReplyType.TEXT
            reply.content = (
                f"✅ 已调整预警阈值为{threshold}小时，"
                f"当持续在线时长超过{trigger_hours}小时时将自动触发预警。"
            )
            
            return reply
            
        except Exception as e:
            logger.error(f"[AutoSessionWarning] 设置阈值失败: {e}")
            reply = Reply()
            reply.type = ReplyType.TEXT
            reply.content = f"❌ 设置阈值失败: {str(e)}"
            return reply
    
    def _handle_warning_test(self, msg) -> Reply:
        """处理预警测试"""
        try:
            # 获取当前登录信息
            self._load_current_login_info()
            
            if not self.current_wxid:
                reply = Reply()
                reply.type = ReplyType.TEXT
                reply.content = "❌ 无法获取当前登录信息，请确保微信已正常登录。"
                return reply
            
            # 获取登录时间和计算在线时长
            login_time = self._get_real_login_time()
            if not login_time:
                reply = Reply()
                reply.type = ReplyType.TEXT
                reply.content = "❌ 无法获取登录时间信息。"
                return reply
            
            # 计算在线时长
            now = datetime.now()
            online_duration = now - login_time
            online_hours = online_duration.total_seconds() / 3600
            
            # 计算预计掉线时间
            remaining_hours = self.session_duration_hours - online_hours
            
            # 构建测试消息
            test_message = (
                f"⚠️ 掉线预警测试\n"
                f"您已持续在线超过{online_hours:.0f}小时，"
                f"预计{remaining_hours:.0f}小时内即将掉线。\n"
                f"为避免服务中断，请手动扫码重新登录！"
                f"稍后将为您发送登录二维码。"
            )
            
            # 启动异步任务发送二维码
            target_wxid = msg.from_user_id if hasattr(msg, 'from_user_id') else msg.sender_wxid
            threading.Thread(
                target=lambda: asyncio.run(self._send_qr_code_after_delay(target_wxid))
            ).start()
            
            reply = Reply()
            reply.type = ReplyType.TEXT
            reply.content = test_message
            
            return reply
            
        except Exception as e:
            logger.error(f"[AutoSessionWarning] 预警测试失败: {e}")
            reply = Reply()
            reply.type = ReplyType.TEXT
            reply.content = f"❌ 预警测试失败: {str(e)}"
            return reply
    

    
    async def _send_qr_code_after_delay(self, to_wxid: str):
        """延迟发送二维码（异步）"""
        try:
            # 等待1秒
            await asyncio.sleep(1)
            
            # 生成并发送二维码
            success = await self._send_login_qr_code(to_wxid)
            if success:
                logger.info(f"[AutoSessionWarning] 测试二维码发送成功: {to_wxid}")
            else:
                logger.error(f"[AutoSessionWarning] 测试二维码发送失败: {to_wxid}")
        except Exception as e:
            logger.error(f"[AutoSessionWarning] 延迟发送二维码失败: {e}")
    
    def _load_current_login_info(self):
        """从wx849_device_info.json文件加载当前登录信息"""
        try:
            device_info_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 
                "wx849_device_info.json"
            )
            
            if os.path.exists(device_info_path):
                with open(device_info_path, "r", encoding="utf-8") as f:
                    device_info = json.load(f)
                
                self.current_wxid = device_info.get("wxid", "")
                self.current_device_id = device_info.get("device_id", "")
                
                logger.debug(f"[AutoSessionWarning] 已加载登录信息: wxid={self.current_wxid}")
            else:
                logger.warning(f"[AutoSessionWarning] 设备信息文件不存在: {device_info_path}")
                
        except Exception as e:
            logger.error(f"[AutoSessionWarning] 加载登录信息失败: {e}")
    
    def _get_real_login_time(self) -> Optional[datetime]:
        """获取真正的登录时间"""
        try:
            # 优先从wx849_device_info.json获取
            device_info_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 
                "wx849_device_info.json"
            )
            
            if os.path.exists(device_info_path):
                with open(device_info_path, "r", encoding="utf-8") as f:
                    device_info = json.load(f)
                
                login_time_timestamp = device_info.get("login_time", 0)
                if login_time_timestamp > 0:
                    login_time = datetime.fromtimestamp(login_time_timestamp)
                    logger.debug(f"[AutoSessionWarning] 获取到登录时间: {login_time}")
                    return login_time
            
            # 回退方案：从login_stat.json获取
            possible_paths = [
                os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 
                           "lib", "wx849", "WechatAPI", "Client", "login_stat.json"),
                os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 
                           "lib", "wx849", "WechatAPI", "Client2", "login_stat.json"),
                os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 
                           "lib", "wx849", "WechatAPI", "Client3", "login_stat.json"),
            ]
            
            for path in possible_paths:
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        login_stat = json.load(f)
                    
                    login_time_timestamp = login_stat.get("login_time", 0)
                    if login_time_timestamp > 0:
                        login_time = datetime.fromtimestamp(login_time_timestamp)
                        logger.debug(f"[AutoSessionWarning] 从备用路径获取登录时间: {login_time}")
                        return login_time
            
            logger.warning("[AutoSessionWarning] 无法获取登录时间")
            return None
            
        except Exception as e:
            logger.error(f"[AutoSessionWarning] 获取登录时间失败: {e}")
            return None
    
    async def _send_login_qr_code(self, to_wxid: str) -> bool:
        """生成并发送登录二维码"""
        try:
            # 生成设备信息
            device_id = self._create_device_id()
            device_name = self._create_device_name()
            
            # 获取二维码
            async with aiohttp.ClientSession() as session:
                url = f"{self.base_url}/Login/GetQR"
                json_param = {
                    "DeviceName": device_name,
                    "DeviceID": device_id
                }
                
                async with session.post(url, json=json_param, timeout=15) as response:
                    if response.status != 200:
                        logger.error(f"[AutoSessionWarning] 获取二维码HTTP失败: {response.status}")
                        return False
                    
                    result = await response.json()
                    if result.get("Success"):
                        data = result.get("Data", {})
                        qr_url = data.get("QrUrl", "")
                        uuid = data.get("Uuid", "")
                        
                        if qr_url and uuid:
                            # 下载并发送二维码图片
                            qr_image_path = await self._download_qr_image(qr_url, uuid)
                            if qr_image_path:
                                success = await self._send_image_message(to_wxid, qr_image_path)
                                
                                # 清理临时文件
                                try:
                                    if os.path.exists(qr_image_path):
                                        os.remove(qr_image_path)
                                except:
                                    pass
                                
                                return success
            
            return False
            
        except Exception as e:
            logger.error(f"[AutoSessionWarning] 发送登录二维码失败: {e}")
            return False
    
    async def _download_qr_image(self, qr_url: str, uuid: str) -> Optional[str]:
        """下载二维码图片"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(qr_url, timeout=10) as response:
                    if response.status == 200:
                        # 保存到临时文件
                        temp_dir = os.path.join(
                            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 
                            "tmp"
                        )
                        os.makedirs(temp_dir, exist_ok=True)
                        
                        image_path = os.path.join(temp_dir, f"qr_{uuid}_{int(time.time())}.png")
                        
                        with open(image_path, "wb") as f:
                            f.write(await response.read())
                        
                        logger.info(f"[AutoSessionWarning] 二维码下载成功: {image_path}")
                        return image_path
            
            return None
            
        except Exception as e:
            logger.error(f"[AutoSessionWarning] 下载二维码图片失败: {e}")
            return None
    
    async def _send_image_message(self, to_wxid: str, image_path: str) -> bool:
        """发送图片消息"""
        try:
            if not self.current_wxid:
                self._load_current_login_info()
            
            # 检查图片文件是否存在
            if not os.path.exists(image_path):
                logger.error(f"[AutoSessionWarning] 图片文件不存在: {image_path}")
                return False
            
            # 读取图片文件并进行Base64编码
            with open(image_path, "rb") as f:
                image_data = f.read()
                image_base64 = base64.b64encode(image_data).decode('utf-8')
            
            # 使用正确的API端点和参数格式发送图片
            async with aiohttp.ClientSession() as session:
                url = f"{self.base_url}/Msg/UploadImg"
                
                # 使用与wx849_channel.py完全相同的参数格式和顺序
                json_param = {
                    "ToWxid": to_wxid,            # 接收者在前
                    "Base64": image_base64,       # Base64在中间  
                    "Wxid": self.current_wxid     # 发送者在后
                }
                
                logger.debug(f"[AutoSessionWarning] 图片上传请求: URL={url}")
                logger.debug(f"[AutoSessionWarning] 图片大小: {len(image_data)} bytes")
                logger.debug(f"[AutoSessionWarning] 参数: Wxid={self.current_wxid}, ToWxid={to_wxid}")
                
                # 使用json=json_param格式，与成功的实现一致
                async with session.post(url, json=json_param, timeout=30) as response:
                    if response.status != 200:
                        logger.error(f"[AutoSessionWarning] 发送图片HTTP失败: {response.status}")
                        return False
                    
                    try:
                        result = await response.json()
                        logger.debug(f"[AutoSessionWarning] 图片上传响应: {result}")
                        
                        if result.get("Success"):
                            logger.info("[AutoSessionWarning] 图片消息发送成功")
                            return True
                        else:
                            error_msg = result.get("Message", "未知错误")
                            logger.error(f"[AutoSessionWarning] 图片消息发送失败: {error_msg}")
                            return False
                    except json.JSONDecodeError as e:
                        response_text = await response.text()
                        logger.error(f"[AutoSessionWarning] 图片消息响应解析失败: {e}")
                        logger.error(f"[AutoSessionWarning] 响应内容: {response_text}")
                        return False
            
        except Exception as e:
            logger.error(f"[AutoSessionWarning] 发送图片消息异常: {e}")
            import traceback
            logger.error(f"[AutoSessionWarning] 异常详情: {traceback.format_exc()}")
            return False
    
    def _create_device_id(self) -> str:
        """生成设备ID"""
        s = ''.join(random.choice(string.ascii_letters) for _ in range(15))
        md5_hash = hashlib.md5(s.encode()).hexdigest()
        return "49" + md5_hash[2:]
    
    def _create_device_name(self) -> str:
        """生成设备名称"""
        first_names = ["Alice", "Bob", "Charlie", "Diana", "Edward", "Fiona"]
        last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia"]
        return random.choice(first_names) + " " + random.choice(last_names) + "'s iPad"
    
    def _start_background_check(self):
        """启动后台检查"""
        if self.is_running:
            return
        
        self.is_running = True
        self.background_thread = threading.Thread(target=self._background_check_loop, daemon=True)
        self.background_thread.start()
        logger.info("[AutoSessionWarning] 后台预警检查已启动")
    
    def _stop_background_check(self):
        """停止后台检查"""
        self.is_running = False
        if self.background_thread and self.background_thread.is_alive():
            self.background_thread.join(timeout=5)
        logger.info("[AutoSessionWarning] 后台预警检查已停止")
    
    def _background_check_loop(self):
        """后台检查循环"""
        logger.info("[AutoSessionWarning] 后台预警检查线程已启动")
        
        while self.is_running:
            try:
                # 检查是否需要发送预警
                if self._should_send_warning():
                    asyncio.run(self._send_auto_warning())
                
                # 等待检查间隔
                for _ in range(int(self.check_interval_hours * 3600)):
                    if not self.is_running:
                        break
                    time.sleep(1)
                    
            except Exception as e:
                logger.error(f"[AutoSessionWarning] 后台检查循环异常: {e}")
                time.sleep(60)  # 出错后等待1分钟
    
    def _should_send_warning(self) -> bool:
        """判断是否应该发送预警"""
        try:
            if not self.warning_enabled or not self.warning_target:
                return False
            
            # 获取登录时间
            login_time = self._get_real_login_time()
            if not login_time:
                return False
            
            # 计算在线时长
            now = datetime.now()
            online_duration = now - login_time
            online_hours = online_duration.total_seconds() / 3600
            
            # 检查是否达到预警阈值
            trigger_hours = self.session_duration_hours - self.warning_threshold
            
            if online_hours >= trigger_hours:
                # 检查是否已经发送过预警（避免重复发送）
                current_time = time.time()
                if current_time - self.last_warning_time > 3600:  # 至少间隔1小时
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"[AutoSessionWarning] 检查预警条件失败: {e}")
            return False
    
    async def _send_auto_warning(self):
        """发送自动预警"""
        try:
            self._load_current_login_info()
            
            # 获取登录时间和计算在线时长
            login_time = self._get_real_login_time()
            if not login_time:
                return
            
            now = datetime.now()
            online_duration = now - login_time
            online_hours = online_duration.total_seconds() / 3600
            remaining_hours = self.session_duration_hours - online_hours
            
            # 构建预警消息
            warning_text = (
                f"⚠️ 登录状态预警\n\n"
                f"您已持续在线超过{online_hours:.1f}小时，"
                f"预计{remaining_hours:.1f}小时内即将掉线。\n\n"
                f"为避免服务中断，请手动扫码重新登录！\n"
                f"稍后将为您发送登录二维码。"
            )
            
            # 发送文本消息
            text_success = await self._send_text_message(self.warning_target, warning_text)
            
            if text_success:
                logger.info("[AutoSessionWarning] 自动预警文本消息发送成功")
                
                # 等待1秒后发送二维码
                await asyncio.sleep(1)
                
                # 发送二维码
                qr_success = await self._send_login_qr_code(self.warning_target)
                
                if qr_success:
                    logger.info("[AutoSessionWarning] 自动预警二维码发送成功")
                else:
                    logger.warning("[AutoSessionWarning] 自动预警二维码发送失败")
                
                # 更新最后预警时间
                self.last_warning_time = time.time()
            else:
                logger.error("[AutoSessionWarning] 自动预警文本消息发送失败")
                
        except Exception as e:
            logger.error(f"[AutoSessionWarning] 发送自动预警失败: {e}")
    
    async def _send_text_message(self, to_wxid: str, content: str) -> bool:
        """发送文本消息"""
        try:
            if not self.current_wxid:
                self._load_current_login_info()
            
            async with aiohttp.ClientSession() as session:
                url = f"{self.base_url}/Msg/SendTxt"
                json_param = {
                    "Wxid": self.current_wxid,
                    "ToWxid": to_wxid,
                    "Content": content,
                    "Type": 1,
                    "At": ""
                }
                
                async with session.post(url, json=json_param, timeout=10) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result.get("Success", False)
            
            return False
            
        except Exception as e:
            logger.error(f"[AutoSessionWarning] 发送文本消息失败: {e}")
            return False
        
    def get_help_text(self, **kwargs):
        """获取帮助文本"""
        return (
            "🔔 自动掉线预警插件帮助\n\n"
            "指令说明：\n"
            "• $预警状态 - 查询当前在线状态和预计掉线时间\n"
            "• $预警配置 - 获取当前预警配置信息\n"
            "• $预警启用 - 开启自动掉线预警功能\n"
            "• $预警禁用 - 关闭自动掉线预警功能\n"
            "• $预警阈值 xh - 设置预警阈值（如：$预警阈值 2h）\n"
            "• $预警测试 - 手动进行掉线预警测试\n\n"
            "功能说明：\n"
            "- 自动监控微信登录状态\n"
            "- 在即将掉线前自动发送预警\n"
            "- 支持手动查询和测试\n"
            "- 配置保存在插件目录的config.json中\n"
        )
    
    def reload(self):
        """重新加载插件"""
        # 停止后台检查
        self._stop_background_check()
        
        # 重新加载配置
        self.config = super().load_config()
        if not self.config:
            self.config = self._load_default_config()
        
        # 更新配置变量
        self.warning_enabled = self.config.get("auto_session_warning_enabled", True)
        self.warning_threshold = self.config.get("auto_session_warning_threshold", 2)
        self.warning_target = self.config.get("auto_session_warning_target", "")
        
        # 如果预警启用，重新启动后台检查
        if self.warning_enabled:
            self._start_background_check()
        
        logger.info("[AutoSessionWarning] 插件已重新加载")
    
    def __del__(self):
        """析构函数"""
        try:
            self._stop_background_check()
        except:
            pass 