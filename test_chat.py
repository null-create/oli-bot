from openai import OpenAI, AzureOpenAI
from openai.types.chat import ChatCompletionMessage
from openai.types.responses import Response
from typing import Any

API_KEY = "weeee"
HOST = "http://localhost:9734/v1"


# Parses a streaming response and returns a dictionary with the content.
def _handle_streaming_response(response: Any) -> dict:
    response_object = {
        "content": "",
        "tool_calls": [
            {
                "id": None,
                "type": "function",
                "function": {
                    "arguments": "",
                    "name": None,
                },
            }
        ],
    }

    for part in response:
        if part.choices:
            delta = part.choices[0].delta
            finish_reason = part.choices[0].finish_reason
            if finish_reason and finish_reason != "":
                response_object["finish_reason"] = finish_reason

            if delta.content:
                response_object["content"] += delta.content
                print(delta.content, end="", flush=True)

            if delta.tool_calls:
                tool_call = delta.tool_calls[0]
                if tool_call.id:
                    response_object["tool_calls"][0]["id"] = tool_call.id
                if tool_call.function.name:
                    response_object["tool_calls"][0]["function"][
                        "name"
                    ] = tool_call.function.name
                if tool_call.function.arguments:
                    response_object["tool_calls"][0]["function"][
                        "arguments"
                    ] += tool_call.function.arguments
    print()
    return response_object

def test_chat_completions() -> None:
    try:
        client = OpenAI(
            api_key=API_KEY,
            base_url=HOST,
        )
        response: ChatCompletionMessage = client.chat.completions.create(
            model="global.anthropic.claude-sonnet-4-6",
            messages=[{"role": "user", "content": "Hello, how are you?"}],
            stream=False,
        )
        print("Chat Completion Response:", response.choices[0].message.content)
    except Exception as e:
        print("Error during chat completion:", str(e))

def test_chat_completions_streaming() -> None:
    try:
        client = OpenAI(
            api_key=API_KEY,
            base_url=HOST,
        )
        response = client.chat.completions.create(
            model="global.anthropic.claude-sonnet-4-6",
            messages=[{"role": "user", "content": "Hello, how are you?"}],
            stream=True,
        )
        print("Streaming Chat Completion Response:")
        _handle_streaming_response(response)

    except Exception as e:
        print("Error during chat completion:", str(e))


if __name__ == "__main__":
    test_chat_completions_streaming()
