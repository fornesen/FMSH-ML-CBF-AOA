# NPU 调制识别与训练源码

当前识别类别固定为 FM、AM、CW、NOISE、OOK、PM。神经网络只负责调制识别，不替代 ML–CBF 测角或两组 ML 定位。

## 板端输入和运行

四帧输入均来自 PL 实际波束合成的 PHA I/Q。PS 每轮去掉前 64 点，对 4032 个有效样本进行特征处理；跨轮保持公共幅度尺度，不将不同帧间隔直接拼成连续采样。

三种输入包括时频、频谱和序列特征，按配套模型契约的形状、顺序和归一化执行。模型契约保存在运行包 `models/`，必须与 JSON/RAW 一起更新。模型身份、类别数和输入输出契约属于运行接口，不能作为普通历史报告删除。

NPU 消费者源码为 `PS/npu/src/`、`include/`，ARM64 构建入口：

```sh
sh PS/npu/build_board.sh
```

使用 Icraft 3.36.1 开发环境。构建输出是 `PS/build/npu/icraft_npu_yolo`。匹配的程序、模型及库放入本地 `assets/board/`，由统一部署打包器收集。HOST_ONLY 构建只供主机特征和协议测试，不代表硬件推理。

## 训练与转换

`PS/npu/training/` 保留信号生成、特征、网络结构、训练和转换源码。`pm_v8/` 是当前六分类流程；`pm_v7/` 中仍被导入的信号和工具模块属于共享依赖，不能仅凭旧编号删除。

主要入口：

| 文件 | 用途 |
|---|---|
| `pm_v8/build_dataset.py` | 构建六类数据集与划分。 |
| `pm_v8/prepare_distilled.py`、`train_distilled.py` | 准备训练数据、训练模型。 |
| `pm_v8/evaluate.py` | 评估浮点模型。 |
| `pm_v8/compile_icraft.py`、`validate_compiled.py` | Icraft 编译与数值对照。 |
| `classification_models.py`、`classification_features.py` | 网络结构和特征实现。 |

训练另需 PyTorch、NumPy、SciPy、匹配的 YOLO/Icraft 源环境、预训练权重和自行准备的数据；这些不是日常上位机依赖。通过 `YOLOV5_ICRAFT_ROOT` 指定该源环境，Icraft SDK 通过 `--sdk` 或 `ICRAFT_ROOT` 指定（PowerShell 构建入口使用 `-IcraftRoot`）。参考数据通过 `AOA_REFERENCE_DATA` 指定，实验输出通过 `AOA_PM_EXPERIMENT` 指定；不在源码中保存某台电脑的个人目录、原始实测文件或历史报告。

比较精度时分开训练、验证和独立测试数据。模型重新训练后须重新转换并核对数值，不能只替换权重文件或调类别显示。
