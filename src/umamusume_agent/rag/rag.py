import os
import pickle
from typing import List, Optional, Dict, Any
# from langchain.vectorstores import FAISS
from langchain_community.vectorstores import FAISS
# from langchain.embeddings import HuggingFaceEmbeddings
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
# from langchain.document_loaders import CSVLoader, TextLoader, PyMuPDFLoader, DirectoryLoader
from langchain_community.document_loaders import CSVLoader, TextLoader, PyMuPDFLoader, DirectoryLoader
from langchain.docstore.document import Document
from ..config import config
class RAGManager:
    def __init__(self):
        # 从环境变量读取配置
        rag_dir = config.RAG_DIRECTORY
        if not os.path.exists(rag_dir):
            raise FileNotFoundError(f"RAG directory not found: {rag_dir}")
        self.rag_directory = rag_dir
        self.cache_file = os.path.join(self.rag_directory, 'vectorstore_cache.pkl')
        self.vectorstore = None
        self.config = self._load_config()
        
    def _load_config(self) -> Dict[str, Any]:
        """加载配置"""
        return {
            'chunk_size': int(os.getenv('CHUNK_SIZE', '500')),
            'chunk_overlap': int(os.getenv('CHUNK_OVERLAP', '100')),
            'hf_embedding_model': os.getenv('HF_EMBEDDING_MODEL', 'Qwen/Qwen3-Embedding-0.6B'),
            'device': os.getenv('EMBEDDING_DEVICE', 'cpu')
        }
    
    def load_csv_with_description(self, csv_path: str, description_path: str) -> List[Document]:
        """加载CSV文件和对应的描述文件"""
        documents = []
        
        # 加载描述文件
        if os.path.exists(description_path):
            with open(description_path, "r", encoding="utf-8") as file:
                description = file.read()
            metadata_doc = Document(page_content=description, metadata={"source": "description"})
            documents.append(metadata_doc)
        
        # 加载CSV文件
        if os.path.exists(csv_path):
            loader = CSVLoader(csv_path, encoding='utf-8')
            csv_documents = loader.load()
            documents.extend(csv_documents)
            
        return documents
    
    def load_single_md(self, md_path: str) -> List[Document]:
        """加载单个Markdown文件"""
        documents = []
        if os.path.exists(md_path):
            loader = TextLoader(md_path, encoding='utf-8')
            documents = loader.load()
        return documents
    
    def load_single_pdf(self, pdf_path: str) -> List[Document]:
        """加载单个PDF文件"""
        documents = []
        if os.path.exists(pdf_path):
            loader = PyMuPDFLoader(pdf_path)
            documents = loader.load()
        return documents
    
    def load_directory_documents(self, file_types: List[str] = None) -> List[Document]:
        """加载目录中的所有文档"""
        if file_types is None:
            file_types = ['**/*.txt', '**/*.md', '**/*.csv']
            
        documents = []
        for file_type in file_types:
            loader = DirectoryLoader(
                self.rag_directory,
                glob=file_type,
                show_progress=True
            )
            try:
                docs = loader.load()
                documents.extend(docs)
            except Exception as e:
                print(f"加载 {file_type} 文件时出错: {e}")
                
        return documents
    
    def load_documents_by_mode(self, mode: str = "auto") -> List[Document]:
        """根据模式加载文档"""
        if mode == "csv_with_description":
            csv_path = os.path.join(self.rag_directory, 'umamusume_character_baseinfo.csv')
            desc_path = os.path.join(self.rag_directory, 'umamusume_character_baseinfo.md')
            return self.load_csv_with_description(csv_path, desc_path)
            
        elif mode == "single_md":
            md_path = os.path.join(self.rag_directory, 'umamusume_game_info.md')
            return self.load_single_md(md_path)
            
        elif mode == "single_pdf":
            pdf_path = os.path.join(self.rag_directory, 'document.pdf')
            return self.load_single_pdf(pdf_path)
            
        elif mode == "directory":
            return self.load_directory_documents()
            
        else:  # auto mode - 自动检测
            documents = []
            
            # 检查是否有CSV+描述文件组合
            csv_path = os.path.join(self.rag_directory, 'umamusume_character_baseinfo.csv')
            desc_path = os.path.join(self.rag_directory, 'umamusume_character_baseinfo.md')
            if os.path.exists(csv_path) and os.path.exists(desc_path):
                documents.extend(self.load_csv_with_description(csv_path, desc_path))
            
            # 检查单个MD文件
            md_path = os.path.join(self.rag_directory, 'umamusume_game_info.md')
            if os.path.exists(md_path):
                documents.extend(self.load_single_md(md_path))
            
            # 检查PDF文件
            pdf_files = [f for f in os.listdir(self.rag_directory) if f.endswith('.pdf')]
            for pdf_file in pdf_files:
                pdf_path = os.path.join(self.rag_directory, pdf_file)
                documents.extend(self.load_single_pdf(pdf_path))
                
            return documents
    
    def split_documents(self, documents: List) -> List:
        """分割文档"""
        separators = ["\n\n", "\n", "。", "！", "？", "，", " ", ""]
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.config['chunk_size'],
            chunk_overlap=self.config['chunk_overlap'],
            length_function=len,
            add_start_index=True,
            separators=separators,
        )
        texts = text_splitter.split_documents(documents)
        return texts
    
    def create_vectorstore(self, texts: List):
        """创建向量数据库"""
        hf_embedding_model = HuggingFaceEmbeddings(
            model_name=self.config['hf_embedding_model'],
            model_kwargs={'device': self.config['device']},
            encode_kwargs={'normalize_embeddings': True}
        )
        self.vectorstore = FAISS.from_documents(texts, hf_embedding_model)
        return self.vectorstore
    
    def save_cache(self):
        """保存向量数据库到本地缓存"""
        if self.vectorstore is None:
            return
            
        os.makedirs(self.rag_directory, exist_ok=True)
        with open(self.cache_file, 'wb') as f:
            pickle.dump(self.vectorstore, f)
        print(f"向量数据库已缓存到: {self.cache_file}")
    
    def load_cache(self) -> bool:
        """从本地缓存加载向量数据库"""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'rb') as f:
                    self.vectorstore = pickle.load(f)
                print("已从本地缓存加载向量数据库")
                return True
            except Exception as e:
                print(f"加载缓存失败: {e}")
                return False
        return False
    
    def initialize_rag(self, mode: str = "auto", force_rebuild: bool = False):
        """初始化RAG系统"""
        # 如果强制重建或缓存不存在，则重新构建
        if force_rebuild or not self.load_cache():
            print(f"正在构建向量数据库 (模式: {mode})...")
            documents = self.load_documents_by_mode(mode)
            if not documents:
                raise ValueError("未找到任何文档进行加载")
            texts = self.split_documents(documents)
            self.create_vectorstore(texts)
            self.save_cache()
        else:
            print("使用缓存的向量数据库")
    
    def search(self, query: str, k: int = 4) -> List:
        """搜索相关文档"""
        if self.vectorstore is None:
            raise ValueError("向量数据库未初始化，请先调用 initialize_rag()")
        
        results = self.vectorstore.similarity_search(query, k=k)
        return results
    
    def search_with_scores(self, query: str, k: int = 4) -> List:
        """搜索相关文档并返回相似度分数"""
        if self.vectorstore is None:
            raise ValueError("向量数据库未初始化，请先调用 initialize_rag()")
        
        results_with_scores = self.vectorstore.similarity_search_with_score(query, k=k)
        return results_with_scores

# 全局实例
rag_manager = RAGManager()

# 便捷函数
def initialize_rag(mode: str = "auto", force_rebuild: bool = False):
    """初始化RAG
    Args:
        mode: 加载模式
            - "auto": 自动检测 (默认)
            - "csv_with_description": CSV文件+描述文件
            - "single_md": 单个Markdown文件
            - "single_pdf": 单个PDF文件
            - "directory": 目录中的所有文档
        force_rebuild: 是否强制重建缓存
    """
    rag_manager.initialize_rag(mode, force_rebuild)

def search(query: str, k: int = 4) -> List:
    """搜索"""
    return rag_manager.search(query, k)

def search_with_scores(query: str, k: int = 4) -> List:
    """搜索并返回分数"""
    return rag_manager.search_with_scores(query, k)