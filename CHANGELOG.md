# CHANGELOG

## 2026-10-16

- `inquiry__conclude` 允许省略没有使用的 `calculations`，同轮参数修复后仍保留并重验此前合法的计算。工具说明提供最小参数示例。
- 模型配置增加 `decide`、`review`、`completion` 三个阶段。旧复核模型设置继续生效，普通决策和预算收尾使用各自配置，日志与回复解析使用本次调用实际选中的模型。
