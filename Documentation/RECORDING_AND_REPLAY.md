# 调试归档与离线回放

归档是主上位机内部功能，只有一个日常上位机。原始长期数据全部保存在电脑，板端只使用有容量上限的 RAM 暂存。

## 开关

调试归档默认关闭。关闭时不生成新原件、不后台打包或校验、不继续发起原始数据传输。波形和频谱有各自的上报开关。开启归档后，电脑注册的接收端保存并确认数据；没有确认的原件暂留 RAM。关闭暂停待发任务，重新开启可续传，已发出的单个请求最多等待 3 秒。

## 同批关联

每份完整归档用批次标识关联四轮：

- 7100 六路原始 ADC 字节；
- 复旦微 PL 输入、六路 FFT 原始输出和 PHA；
- 本批实际采集参数、预处理标识、PS 最终结果；
- NPU 交接信息；最终分类通过同批遥测关联，归档中存在识别结果时一并读取。

归档中的原件不随显示模式改变，不对已有 FFT 重复加窗，不把缺帧或不同批次拼成完整批。原始 FFT 的预处理标识区分旧软件窗和当前 PL 窗。

## 电脑回放

内部入口为 `PC/Console/recordings/capture.py`，配套原生回放程序在同目录 `bin/`，由当前 PS 数学源码构建。所有命令从部署包根目录运行：

```sh
runtime/python/python.exe -B PC/Console/recordings/capture.py list PC/data/hardware_7100/board
runtime/python/python.exe -B PC/Console/recordings/capture.py verify PATH_TO_CAPTURE.archive.json
runtime/python/python.exe -B PC/Console/recordings/capture.py replay PATH_TO_CAPTURE.archive.json --output PC/data/replay/example
```

用真实归档文件替换 `PATH_TO_CAPTURE.archive.json`，输出使用新目录。回放重新解码同批 ADC/PL 并运行 PS 数学，与记录结果比较；不访问设备，也不重新采样。可选 `--export-npz` 需要额外 NumPy，只用于研究分析，不是日常上位机的依赖。
