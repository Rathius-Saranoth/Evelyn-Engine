# Web Search DocString Thoughts

"Only call this when the user asks about current events, real-time data, or explicitly asks you to look something up."

## Web Search Tool

### Description

Performs a web search using the DuckDuckGo search engine. This tool should only be used when the user explicitly requests information that requires up-to-date knowledge of current events, real-time data, or specific facts that may not be present in the model's training data.

### Usage

```python
web_search(query: str) -> str
```

### Parameters

- `query` (str): The search query to use. This should be a concise and specific description of the information needed.

### Returns

- `str`: The search results, formatted as a summary of the top results found.

### Examples

```python
# Example 1: Current events
web_search("What is the current score of the Lakers game?")

# Example 2: Real-time data
web_search("What is the current weather in San Francisco?")

# Example 3: Specific facts
web_search("Who won the Nobel Prize in Physics in 2023?")
```

### Important Notes

- Only use this tool when absolutely necessary. Overuse can slow down responses and may not always provide the most accurate information.
- For general knowledge questions that do not require real-time data, rely on the model's internal knowledge base.
- Always prioritize using the `vault_search` tool for information retrieval from the local knowledge base.
