# Linux 一台电脑追踪测试

此测试用一台 Linux 桌面电脑模拟 PC4 → PC2 → PC1 → PC4，最后通过 USB 串口控制真正的 Arduino Uno。摄像头和 Arduino 都连接到这台电脑，Arduino 先上传 `device/cctv_tracking_servo/face_tracking_servo.ino`。关闭 Arduino IDE 串口监视器。

在仓库根目录运行：

```bash
python3 test/1desktoptest.py
```

脚本会寻找 Arduino 串口，创建 `test/.venv`（如有需要），安装缺少的 Python 包，寻找或下载 `yolo11n.pt`，自动启动 PC4 Bridge、PC1 追踪接收器、PC2 检测程序和 PC4 摄像头发送程序。视频窗口按 `q` 退出；终端显示 `TRACK` 以及 PC1 的转发状态。

如果连接了多个 USB 串口设备，指定 Arduino 端口：

```bash
python3 test/1desktoptest.py --serial-port /dev/ttyACM0 --camera 0
```

Linux 需要可用的摄像头、图形桌面和串口访问权限。若创建虚拟环境失败，先安装发行版的 `python3-venv` 包。模型首次下载和依赖安装需要网络。

测试使用仓库里的 PC2、PC4 脚本和 Main Server 的 `TrackingBridge`/`PC2TCPReceiver`，所以测试的坐标协议和实际三机通信一致。此脚本只模拟 PC1 的追踪部分；不会启动 PostgreSQL，也不验证身份识别、权限判断或自动开门授权。
