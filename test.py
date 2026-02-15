import os
from openai import OpenAI

# 配置中转站参数
client = OpenAI(
    api_key="sk-qVTUBF2eI86CT6zCa4C40mxoOXrrjGAVNLi6fhSnRr0uB8JL", 
    base_url="https://api.openai-proxy.org/v1"  # 注意加上 /v1
)

def verify_proxy_llm():
    # 验证逻辑：询问只有联网才能知道的实时“随机性”信息
    # 如果模型能联网，它会说出具体的实时金价、天气或重大新闻
    # 如果不能联网，它会回答“无法获取实时信息”或基于旧数据瞎编（幻觉）
    test_prompt = "请查询并告诉我：此时此刻（2026年2月8日）黄金的实时市场价格是多少？请给出具体数值和来源。"

    print(f"正在通过中转站 [{client.base_url}] 发送验证指令...\n")
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o",  # 或者是 gpt-3.5-turbo 等
            messages=[
                {"role": "user", "content": test_prompt}
            ],
            temperature=0 # 使用 0 以获得最确定的回答
        )
        
        answer = response.choices[0].message.content
        print("--- 验证结果 ---")
        print(answer)
        print("----------------")
        
        # 自动化判定逻辑
        indicators = ["无法访问", "实时信息", "截止日期", "截止到", "我不能上网"]
        if any(word in answer for word in indicators):
            print("\n✅ 结论：该模型【没有】搜索功能。符合原生 API 的预期。")
        else:
            print("\n❌ 结论：模型给出了具体数值。请手动搜索核对该数值是否准确：")
            print("1. 如果数值准确 -> 说明该模型被中转站或底层模型注入了搜索插件。")
            print("2. 如果数值离谱 -> 说明模型在产生'幻觉'（瞎编），在自动化构建中这非常危险。")

    except Exception as e:
        print(f"调用出错，请检查 KEY 或中转站地址: {e}")

if __name__ == "__main__":
    verify_proxy_llm()