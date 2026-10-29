# 构建与部署

所有命令从工程根目录执行。电脑源码工程保存完整开发文件；复旦微只安装运行包，目标目录为 `/root/aoa_system/V2.0`。PC 页面、训练代码、测试、原始归档和 Vivado 工程不放到板端。

## 环境

| 目标 | 所需环境 |
|---|---|
| 复旦微 PS / NPU | ARM64 Linux、C++17 编译器、Icraft 3.36.1 开发库。 |
| 离线 PS 数学 | C++17 编译器，不需要板卡或 Icraft 硬件后端。 |
| 日常上位机 | 交付包内便携 Python 与本地网页资源。 |
| FPGA 重建 | Vivado / SDK 2018.3，复旦微项目还需要匹配的 Procise、IP 和授权。 |
| 模型训练 | 独立 Python 训练环境与对应数据，见[NPU](NPU.md)。 |

操作系统、内核驱动、设备节点和网卡配置由板卡系统提供；应用运行库、模型、Python 和 PL 发布镜像集中在运行包中。

## PS 与 NPU 编译

在 ARM64/Icraft 开发环境执行：

```sh
sh PS/build_board.sh
sh PS/npu/build_board.sh
```

输出分别为 `PS/build/bin/icraft_aoa_ml3`、`PS/build/npu/icraft_npu_yolo`。脚本只编译，不替换正在运行的程序或下载 PL。资源有限的设备采用串行构建；日常部署设备不保留临时构建目录。

没有 SDK 的开发机可以构建数学或特征处理部分：

```sh
cmake -S PS -B PS/build/host -DICRAFT_AOA_HOST_ONLY=ON
cmake --build PS/build/host --parallel 1
cmake -S PS/npu -B PS/build/npu-host -DPHA_NPU_HOST_ONLY=ON
cmake --build PS/build/npu-host --parallel 1
```

主机模式不表示已执行 PL 或 NPU。编译后的运行程序、匹配模型和应用依赖放入 `assets/board/`，正式配置放入 `Board/config/`。

## 唯一部署包

```sh
python package_release.py --output dist/release/AOA_System_V2.0
```

输出同目录的 `AOA_System_V2.0.zip`。包内含唯一上位机、复旦微 `Board/V2.0.tar.gz` 和 7100 `BOOT.bin`；不含原始 ADC 查看器、开发源码、训练数据、测试和历史报告。

Windows Python 从 `dependencies/windows/python/` 读取。板端程序、模型和应用库从 `assets/board/` 读取；7100 镜像从 `assets/fpga7100/` 读取。Git 源码不包含全部二进制依赖，首次构建需在本地补齐。可从唯一部署包复用：将 `runtime/python/` 放到源码的 `dependencies/windows/python/`；将 `Board/V2.0.tar.gz` 解到 `assets/board/`；将 7100 的 `BOOT.bin` 放到 `assets/fpga7100/`。重新生成硬件镜像仍需匹配的 FPGA/SDK 工具和第三方 IP。

首次安装与服务操作统一见[集中使用说明](GETTING_STARTED.md)。使用 tar 安装板端文件可保留 Linux 执行权限。只把运行包安装到 `/root/aoa_system/V2.0`，其他源码留在开发机。

## 参数和升级范围

正式参数为 `Board/config/job.json`；运行包内对应 `config/job.json`。运行管理器将其转换成 PS/NPU 命令行，核心 ELF 不直接读取此 JSON。

- 采样、RF、本振关系、DDS、基带频带应成套核对。
- `array.csv` 保存阵列几何；`channel_compensation.csv` 或频段补偿表独立加载，不将补偿固化到代码。
- PS/NPU 必须使用相同 IPC 版本与名称；更换模型须同时匹配消费者的类别、输入形状和预处理。
- 观测开关和原始归档分别控制。调试归档默认关闭，原始长期数据只存电脑。

7100 下载、Flash 固化及复旦微 PL 构建是独立操作，见[FPGA 工程](FPGA.md)。更换 FPGA 产物时须同步确认其数据格式、版本和 PS 解码规则。
