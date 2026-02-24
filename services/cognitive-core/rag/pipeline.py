import logging
from langchain.chains import ConversationalRetrievalChain
from langchain.memory import ConversationBufferWindowMemory
from langchain_core.messages import HumanMessage, AIMessage

from llm.client import get_llm
from rag.retriever import get_retriever

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Maya, a helpful nutrition assistant for Mandala Foods Nepal.
Answer questions only about Mandala Foods products, their nutritional content,
ingredients, and benefits. If a question is outside this scope, politely redirect
the user. You support both English and Nepali. If the user writes in Nepali or
code-switches between Nepali and English, respond in the same language they used.
Always base your answers on the retrieved product information provided to you.
Never make up nutritional claims not present in the context."""


def build_chain(conversation_history: list[dict] | None = None):
    """Build a ConversationalRetrievalChain with memory from request history."""
    llm = get_llm()
    retriever = get_retriever(k=4)

    memory = ConversationBufferWindowMemory(
        k=10,
        memory_key="chat_history",
        return_messages=True,
        output_key="answer",
    )

    # Populate memory from conversation history
    if conversation_history:
        for msg in conversation_history:
            if msg["role"] == "user":
                memory.chat_memory.add_message(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                memory.chat_memory.add_message(AIMessage(content=msg["content"]))

    chain = ConversationalRetrievalChain.from_llm(
        llm=llm,
        retriever=retriever,
        memory=memory,
        return_source_documents=True,
        combine_docs_chain_kwargs={"prompt": _build_prompt()},
        verbose=False,
    )
    return chain


def _build_prompt():
    from langchain.prompts import PromptTemplate
    template = f"""{SYSTEM_PROMPT}

Context from knowledge base:
{{context}}

Question: {{question}}

Answer:"""
    return PromptTemplate(
        template=template,
        input_variables=["context", "question"],
    )


async def run_pipeline(
    message: str,
    conversation_history: list[dict] | None = None,
) -> dict:
    """Run the RAG pipeline and return response with sources."""
    chain = build_chain(conversation_history)
    result = chain.invoke({"question": message})

    sources = []
    if result.get("source_documents"):
        for doc in result["source_documents"]:
            source = doc.metadata.get("source", "unknown")
            page = doc.metadata.get("page")
            if page is not None:
                source = f"{source}_page_{page}"
            if source not in sources:
                sources.append(source)

    return {
        "response": result["answer"],
        "sources": sources,
    }
