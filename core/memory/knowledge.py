from uuid import uuid4
from core.models.providers import get_embedding_provider
from core.security.payment_data import redact_payment_data


def ingest(conn, tenant, agent, title, content):
    title,content=redact_payment_data(title),redact_payment_data(content)
    document = uuid4()
    conn.execute('INSERT INTO knowledge_documents(id,tenant_id,agent_id,title,content) VALUES(%s,%s,%s,%s,%s)',
                 (document,tenant,agent,title,content))
    embedder = get_embedding_provider()
    for start in range(0,len(content),800):
        chunk = content[start:start+1000]
        vector = str(embedder.embed(chunk))
        conn.execute('INSERT INTO knowledge_chunks(id,tenant_id,agent_id,document_id,content,embedding) VALUES(%s,%s,%s,%s,%s,%s::vector)',
                     (uuid4(),tenant,agent,document,chunk,vector))
    return document


def retrieve(conn, tenant, agent, query, limit=4):
    # MATERIALIZED fixes the ordering: scope first, then similarity. RLS adds
    # a second independent guard even if an application WHERE is later missed.
    embedder = get_embedding_provider()
    vector = str(embedder.embed(query))
    return conn.execute('''WITH scoped_chunks AS MATERIALIZED (
       SELECT id,document_id,content,embedding FROM knowledge_chunks WHERE tenant_id=%s AND agent_id=%s
      ) SELECT id,document_id,content,1-(embedding <=> %s::vector) AS similarity
        FROM scoped_chunks ORDER BY embedding <=> %s::vector LIMIT %s''',
      (tenant,agent,vector,vector,limit)).fetchall()


def format_retrieved_context(chunks: list[dict]) -> str:
    """Formats retrieved chunks matching the enterprise RAG context contract ({astra_retrieved_context})."""
    if not chunks:
        return 'No verified knowledge context found.'
    blocks = []
    for idx, c in enumerate(chunks, 1):
        doc_id = str(c.get('document_id', f'doc_{idx}'))
        content = c.get('content', '').strip()
        blocks.append(f'[Doc: {doc_id}]\n{content}')
    return '\n\n'.join(blocks)
