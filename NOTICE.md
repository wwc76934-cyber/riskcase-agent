# 来源、使用与发布说明

本仓库当前交付本地研究作品，没有以统一许可证对课程、第三方及新增内容作重新授权；也没有声称已完成公开开源发布的全部权利审查。

## 课程骨架

原Lab01–Lab03的17个白名单文件导入独立Git历史，来源和哈希见[ORIGIN.md](ORIGIN.md)、course-baseline-manifest.json。课程骨架和原49项测试不冒充本项目新增金融贡献。公开发布前，应确认课程材料的许可与允许的署名/发布方式；原课程仓库保持独立。

## IBM TabFormer

数据固定于提交 `ebb7cd68ee1897599568107740bc452104bbbaf8`。上游明确描述为合成信用卡交易，而非银行脱敏流水。仓库代码许可证为Apache-2.0，但本项目尚未单独确证数据归档的商业使用/再分发授权范围；不从代码许可证推导数据已获得所有授权。

依据：[固定版本README](https://github.com/IBM/TabFormer/blob/ebb7cd68ee1897599568107740bc452104bbbaf8/README.md)、[固定版本LICENSE](https://github.com/IBM/TabFormer/blob/ebb7cd68ee1897599568107740bc452104bbbaf8/LICENSE)、[数据目录](https://github.com/IBM/TabFormer/tree/ebb7cd68ee1897599568107740bc452104bbbaf8/data/credit_card)。原始数据、案例事实库和大型本机产物在Git忽略目录，不随仓库分发。

## 轻量演示与模型

app/risk/demo_fixture.py中的少量交易由本项目自建，只服务安装和流程验证；不是IBM样本、真实个人或训练成绩。检测结果图与摘要由实际本机研究产物导出，并明确合成、抽样与时间口径。

真实模型接口由硅基流动提供，模型名Pro/zai-org/GLM-5.1；不称BigModel官方接口。模型、Python包、浏览器及工具遵守各自协议。本机凭据、访问令牌、调用原始文件不作为公开材料。

## 展示材料

可以本地运行、审阅和讲解当前研究结果。公开托管前还需核对课程/数据权利、材料隐私、凭据与运行日志，不把本地可用等同于已获得任意再分发权。
