"""Controller-side authored material; never import from a runtime tool.

Every row is separately written. States describe the target payment P's customer
statement at T1/T2, NOT actual fraud or legal liability. Q is another payment.
The evaluation material is an AUTHOR CANDIDATE, not independently reviewed truth.
"""

DEVELOPMENT = {
 'quoted_speech':[
  ('短信里写“{P}不是本人操作”，我只是把整段转给客服，并没有回答是不是我买的。','请记下我自己的回答：{P}是我事先同意的。','unresolved','authorized'),
  ('同事替我留言说{Q}我不认；关于{P}，他没有问过我。','{P}这单是我本人下的，前面的转述只针对{Q}。','unresolved','authorized'),
  ('客服把骗子说的“这是你同意的{P}”记在我的口述栏，那是对方的话。','我的意思恰好相反，我未许可{P}这次付款。','unresolved','denied'),
  ('我引用旧工单“没有同意{Q}”，不是说今天{P}也有同样问题。','现在才对{P}作答：这单未经我允许。','unresolved','denied'),
  ('来电人自称持卡人，说{P}没同意；接线员还没核验来电身份。','经原登记渠道联系到本人，但本人仍不能辨认{P}。','unresolved','unresolved'),
 ],
 'negation_scope':[
  ('我没有说我同意了{P}，请不要把“没有说”漏掉。','{P}不是我许可的付款，这不是对{Q}的评价。','unresolved','denied'),
  ('不能说{P}不是我批准的；我记得当时点了付款。','核对订单后确认，{P}确实得到我的批准。','unresolved','authorized'),
  ('我没否认{P}，但也没确认，只是还没翻到订单。','找到收据了，{P}是我同意购买的。','unresolved','authorized'),
  ('我不否认自己做了验证，否认的是对{P}的付款许可。','验证是为了查账，我始终没有同意{P}扣款。','denied','denied'),
  ('请删掉“客户否认{P}”，我说的是暂时不能核对。','家人也不清楚{P}，目前我不能确认是否批准过。','unresolved','unresolved'),
 ],
 'payment_scope':[
  ('我给过{Q}的许可，不表示{P}也在范围内；{P}我没答应。','继续按逐笔记录：{Q}认可，{P}不认可。','denied','denied'),
  ('{Q}出了问题，不过{P}那单是我安排的。','我确认的始终是{P}，没有把{Q}一并确认。','authorized','authorized'),
  ('我同意过两单中的一单，但现在分不清编号。','订单号对上了，同意的是{Q}，{P}没有许可。','unresolved','denied'),
  ('{P}与{Q}商户名一样，我只同意{P}。','即使账单描述相同，也请保留我对{P}的确认。','authorized','authorized'),
  ('我认可{Q}的报价，{P}是否包含在内得看原委托。','原委托只列{Q}；{P}不在我批准范围内。','unresolved','denied'),
 ],
 'later_amendment':[
  ('我认不出{P}，目前明确表示我没有同意这笔。','撤回先前否认：找到了自己下单的记录，{P}是我批准的。','denied','authorized'),
  ('看到商户名称时我说{P}应该是自己的。','更正：我认可的是{Q}，刚才把{P}认错了；{P}未获我允许。','unresolved','denied'),
  ('{P}我还在问家人，没有结论。','核对后仍无法辨认，请不要把未回复当认可。','unresolved','unresolved'),
  ('{P}当时是我授权的，只是现在对商品不满意。','我要求售后，但不撤回对{P}当时付款许可的确认。','authorized','authorized'),
  ('我目前明确否认{P}得到过许可，依据是没有找到订单。','订单仍没找到，我撤回确定否认，但尚不能确定批准过。','denied','unresolved'),
 ],
 'channel_verification':[
  ('现号码有人替我回答{P}，但这个号码刚换过，先不要当本人确认。','通过变更前已登记的号码核验本人：我没有许可{P}。','unresolved','denied'),
  ('新设备留言“{P}是本人”，我尚未核验留言者。','独立回访本人确认，{P}是其事先安排。','unresolved','authorized'),
  ('亲属说我大概同意过{P}，这不是我的答复。','原渠道找到本人，本人不能确认{P}。','unresolved','unresolved'),
  ('仅收到了当前联系人的转述，没问到本人对{P}的意愿。','经原登记渠道本人表示，{P}未经自己许可。','unresolved','denied'),
  ('旧号码没人接，新号码称{P}没问题；身份尚未核对。','完成原渠道身份核验后，本人明确认可{P}。','unresolved','authorized'),
 ],
 'limited_export':[
  ('导出没有{P}认证条目，但时间从付款后开始，不能据此说未认证。','本人反馈尚未取得，是否同意{P}仍未确定。','unresolved','unresolved'),
  ('只找到{Q}的录音，里面我同意了付款，不能套给{P}。','补到{P}对应录音：我当时同意了这笔。','unresolved','authorized'),
  ('认证文件有记录，但对应用户被遮掉，还不能确认与{P}的关系。','独立本人回访：没有许可{P}。','unresolved','denied'),
  ('工单只有“客户认可”，缺少它指向{P}还是{Q}的页。','补页指向{P}，本人确认这笔许可属实。','unresolved','authorized'),
  ('没有联系上我不等于我同意{P}；现在仅能记未取得反馈。','后来也没有拿到本人答复，请保留{P}授权缺口。','unresolved','unresolved'),
 ],
}

EVALUATION_CANDIDATES = {
 'conditional_mandate':[
  ('我只同意在先收到报价后支付{P}，目前不知道顺序。','报价是在{P}之后才给的；这笔不符合我许可的条件。','unresolved','denied'),
  ('若家属已确认报价，{P}在我委托内；他还没回复。','家属出示付款前的确认，我认可{P}在原许可内。','unresolved','authorized'),
  ('我同意{Q}，对{P}的许可还以另外一次确认为前提。','至今没有给过那次确认，{P}没有我的付款许可。','unresolved','denied'),
  ('{P}可付，除非商户换了收款对象；我没核对过对象。','核对后收款对象未变，我确认{P}依原许可完成。','unresolved','authorized'),
  ('{P}是否符合我的委托要看原书面约定，不能只凭已验证。','约定文本仍缺失，我仍不能确认{P}。','unresolved','unresolved'),
 ],
 'representative_identity':[
  ('助理说我会认可{P}，但他表示那只是猜测。','本人从既有渠道确认：{P}没有我的许可。','unresolved','denied'),
  ('不是持卡人亲自操作{P}，操作人说得到过委托。','持卡人核实该委托确实覆盖{P}，请记录其确认。','unresolved','authorized'),
  ('接线者称她是持卡人；登记资料核对未通过，对{P}的答复待查。','没有取得经核验持卡人的{P}反馈。','unresolved','unresolved'),
  ('监护人转述“可能买过{P}”，尚未向持卡人核实。','取得可核对本人陈述：{P}是在本人同意后完成。','unresolved','authorized'),
  ('代报人承认没有参与{P}，其否认只能代表自己的认知。','联系到持卡人，持卡人明确否认曾允许{P}。','unresolved','denied'),
 ],
 'revocation_timing':[
  ('我在{P}完成后才取消后续付款许可；{P}本身是我同意的。','取消只对未来有效，我没有否认{P}原来的许可。','authorized','authorized'),
  ('我说过可付，但在{P}之前已明确撤回。','通话时间核对在{P}之前，我未重新给过{P}许可。','denied','denied'),
  ('撤回许可和{P}谁先发生现在看不清，先不要替我下结论。','补件仍缺时分，{P}是否在许可内无法确认。','unresolved','unresolved'),
  ('我取消的是{Q}以后的续订，不是此前单独同意的{P}。','{P}的单次许可没有被那次取消影响。','authorized','authorized'),
  ('取消通知已在{P}前送达，不能用过去的长期许可解释{P}。','本人明确，取消后没有再次许可{P}。','denied','denied'),
 ],
 'correction_pointer':[
  ('我不认可后面那单；前面那单没问题。两笔顺序请再核实。','核对顺序，后面的是{P}：我没许可{P}。','unresolved','denied'),
  ('我之前否认的那笔不是{P}；{P}是本人安排。','更正只涉及另一笔，{P}仍认可。','authorized','authorized'),
  ('“它没授权”中的“它”是我转述的截图标记，不是我对{P}的陈述。','我还没核对{P}，没有新的明确答复。','unresolved','unresolved'),
  ('客服把我说的“后一笔”关联到了{Q}，这个关联可能反了。','纠正关联后，对应{P}，我否认{P}得到过许可。','unresolved','denied'),
  ('我好像同意过较小的一笔，没看明细前请别替我确定编号。','“较小”是刚才说错了：核对后确认我同意的其实是{P}。','unresolved','authorized'),
 ],
 'attempt_and_payment':[
  ('我认可一次失败尝试，不代表认可后来完成的{P}。','失败尝试不是{P}；我没有许可完成的{P}。','unresolved','denied'),
  ('我点过重试，所以不确定{P}是那次重试还是别的付款。','核对订单引用后，{P}就是我同意的那次重试。','unresolved','authorized'),
  ('界面显示失败，我只能确认做过操作，不能确认对应{P}。','商户关联记录未取得，{P}仍无法辨认。','unresolved','unresolved'),
  ('我认可{P}的付款指令，即使最初界面说处理中。','后续显示完成，并不改变我对{P}的许可。','authorized','authorized'),
  ('验证码是为了取消尝试，不能据它认定我批准{P}。','本人再次说明：我没有同意{P}这笔完成付款。','unresolved','denied'),
 ],
 'delegation_boundary':[
  ('我让员工只付{Q}；不能因为卡交给过他就说{P}也许可。','员工没有取得{P}的额外许可。','denied','denied'),
  ('{P}不是我按的，但属于我事前批准给代理人的那次购买。','核对代理订单后，确认{P}在委托内。','authorized','authorized'),
  ('家人能用这张卡不等于本次{P}已问过我，我得再核实。','家人也拿不出本次许可，{P}仍待确认。','unresolved','unresolved'),
  ('只委托一次，{Q}用完后我没有再给许可；{P}是之后的第二次。','我没有另行批准第二次{P}。','denied','denied'),
  ('委托涵盖这两单，但{Q}物品错了；我认可{P}的付款。','对{Q}的商品异议不改变对{P}的批准。','authorized','authorized'),
 ],
 'dependent_followup':[
  ('当前联系人回答的是{Q}；能否使用变更前渠道需要先查变更记录。','完成旧渠道独立回访，持卡人否认许可{P}。','unresolved','denied'),
  ('设备日志将会话标为共享，需先确定要回访的持卡人而非设备使用人。','按持卡人原登记渠道核验，本人认可{P}。','unresolved','authorized'),
  ('工单移交时只剩转述，先找到最初登记的报告人。','原报告人身份仍未核清，{P}的本人反馈未取得。','unresolved','unresolved'),
  ('应用内的确认来自刚找回账户的会话，不宜直接作为{P}本人意愿。','经独立渠道本人说明，{P}未经自己允许。','unresolved','denied'),
  ('客户通过旧渠道问询{Q}，转接后的录音是否涉及{P}需要取原段。','原段查明讨论的是{P}，本人明确认可这笔。','unresolved','authorized'),
 ],
 'deadline_and_unavailability':[
  ('回访排期尚未到，当前没有对{P}的本人结论。','调查截止前仍未完成核验，{P}应保留授权缺口。','unresolved','unresolved'),
  ('可在截止前联系原渠道，对方暂未接听，不应记为默认认可。','原渠道在截止前接通，本人否认{P}。','unresolved','denied'),
  ('唯一回访窗口很短，尚未确认{P}，另一路只是商户转述。','窗口内完成核验，本人认可{P}。','unresolved','authorized'),
  ('对方承诺稍后给{P}材料，承诺本身不是许可。','截止后才到的材料不能用于本次结论，当前仍未核清{P}。','unresolved','unresolved'),
  ('客服只记录等待持卡人回拨，没有取得{P}意愿。','在截止前本人回拨并通过核验，明确否认{P}。','unresolved','denied'),
 ],
}

# Separate language mechanisms for the costly acquisition decision. Neither
# metadata order nor version marks the informative branch. These remain authored
# candidates; textual variety alone does not certify independent business value.
ROUTING = {
 'channel_verification':[
  '{right}在{P}之前已登记。{wrong}在{P}之后才被来电人提出，未核验。',
  '申请书写拟改成{wrong}，但未提交生效；现有的{right}登记没有被替换。',
  '已撤销的是“停用{right}”这张工单，不是撤销{right}登记；{wrong}仍只是候选号码。',
  '独立验证留在{right}的历史记录里。把{wrong}标成已验证的那封信来自未核验来电者，而非核验系统。',
  '对{P}核对的旧档指向{right}。{wrong}是另一份未完成身份检查的更改请求，请勿混为一条登记。',
 ],
 'dependent_followup':[
  '联系人变更在{P}完成之后。对付款当时来说，不能把后来填的{wrong}当作早已验证；当时留存的是{right}。',
  '“{wrong}已验证”出自转来的截图。银行原始登记并无这次核验，付款前独立核实的是{right}。',
  '恢复请求未获批准：没有将{wrong}恢复为可信渠道，{right}的既有有效登记也未失效。',
  '更新流水先提议删{right}改{wrong}，随即在生效前撤回。不能只看提议那一行。',
  '写着“{right}已失效”的备注对应{Q}之后另一次事件；{P}时点的登记仍是{right}，{wrong}尚待身份核实。',
 ],
 'deadline_and_unavailability':[
  '当前导出只有{right}在目标付款前的独立核验。没有记录{wrong}核验，不等于已经核验。',
  'For the payment under review, {right} remained verified. The request naming {wrong} was received later and never authenticated.',
  '名单标题称“新可信渠道”，正文却注明{wrong}尚待核验。旧的{right}没有任何已生效的停用记录。',
  '两次操作不要合并：先核验{right}，后来有人试图登记{wrong}。后一步被挂起，并未推翻前一步。',
  '“不是继续用{right}吗？”是核验员的问句；最终回执确认继续保留{right}，没有批准{wrong}登记。',
 ],
}
