# V3新任务：业务依据与独立性边界

2026-11-02查阅以下一手材料，仅用于审查调查任务是否合理，不复制真实案卷，不引入退款产品或法律裁决。

[英国Financial Ombudsman取证指引](https://www.financial-ombudsman.org.uk/businesses/resolving-complaint/complaints-deal/fraud-scams/unauthorised-transactions-identity-theft)说明调查会结合交易/认证记录、设备或网络情况及通话等多种材料。这里借鉴的是跨来源核对与补证需求；英国责任和赔付规则不写入项目的调查结论。

[新西兰案例84675](https://www.bankomb.org.nz/guides-and-cases/case-notes/84675)与[案例88314](https://www.bankomb.org.nz/guides-and-cases/case-notes/88314)涉及认证码、欺骗情境和后续核实。它们提示要把认证记录、客户知情/同意及机构最终处理分开。项目不引用当事人姓名、金额、原话或最终赔付作为合成案例真值，也不从相似表面信号推断相同责任。

[FOS关于争议交易的说明](https://www.financial-ombudsman.org.uk/businesses/resolving-complaint/complaints-deal/banking-and-payments/disputed-transactions)区分实际同意的付款范围与后来出现的其他扣款。这支持“逐笔授权范围”的任务，但项目不会把商品质量、退款和盗刷混成一个标签。

本轮也核查了CFPB公开投诉来源。[CFPB于2026-08-14的公告](https://www.consumerfinance.gov/about-us/newsroom/the-cfpb-to-cease-discretionary-publication-of-complaint-narratives-and-visualizations/)说明停止原先自愿公布的叙述及可视化，并指出投诉叙述未被逐条验证。因此本轮不沿用旧API假设，不把投诉当确认欺诈标签，也不将真实个人叙述发送模型；新增材料采用完全自建模拟文本。

## 新环境真正增加的机制

同一案件在T0、T1、T2保留不可逆的历史快照；客户后来纠正不能被倒填到T0。每条声明明确关联交易，区分转述者与持卡人、否定范围、委托范围和撤回时间。部分补证有事先声明的模拟人工开销与完成时间，所有条件受同样限制；不将这些模拟单位写成真实银行费用或收益。

开发与候选评测将按文本机制家族、用户及叙述模板分开。参考在控制器，工具只读取当前时点获准原件，所有组包括完整资料LLM都可取得同样材料；有成本来源只能按共同预算限制，必须披露完整资料方案怎样选择可负担资料。

不存在一个有限合成集能数学证明“代码不能完成”：任何公开有限答案都可被硬编码。应检验提前冻结、未读测试参考的通用强代码与模型，并报告语言理解和多步规划的独立贡献。若代码或混合工作流更好，就采用它。

当前没有独立业务人员参与，所有新参考只能标为作者模拟、未经外部专家验证。来源指引不是我们的案例评审，也不能把另一自动程序称为独立人员。新版本不继承旧90案的独立性或分数，候选评测未冻结/审阅前不启动正式价值实验。
