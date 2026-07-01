import os
import time
from datetime import datetime

from openai import OpenAI

from libkit.config import config


class LLMChat:
    def __init__(self, model: str):
        self.model = model

        model_config = config.get_llm_config(self.model)
        self.client = OpenAI(
            base_url=model_config["base_url"], api_key=model_config["key"]
        )

    def chat(self, messages, temperature=0.0, n=1, max_tokens=4096):
        max_retry = 5
        count = 0
        while count < max_retry:
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    n=n,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content, response.usage
            except Exception as e:
                print(f"Error: {e}")
                count += 1
                time.sleep(3)
        return None, None
