"""
Audio Downloader - 基于 Crawl4AI 的音频下载工具
整合自 https://github.com/quantumxiaol/umamusume-voice-data
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Optional, Dict, List, Tuple

import aiofiles
import httpx
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler
from tqdm import tqdm

import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://wiki.biligame.com/umamusume/"


class AudioDownloader:
    """赛马娘音频下载器"""
    
    def __init__(
        self,
        output_root: str = "./audio_downloads",
        request_delay: float = 0.2,
        concurrency: int = 4
    ):
        """
        初始化下载器
        
        Args:
            output_root: 输出根目录
            request_delay: 请求延迟（秒）
            concurrency: 并发数
        """
        self.output_root = Path(output_root)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.request_delay = request_delay
        self.concurrency = concurrency
    
    async def download_file(
        self,
        client: httpx.AsyncClient,
        url: str,
        filepath: str
    ) -> None:
        """下载文件"""
        headers = {"Referer": "https://wiki.biligame.com/"}
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        async with aiofiles.open(filepath, "wb") as f:
            await f.write(resp.content)
    
    async def save_text(self, text: str, filepath: str) -> None:
        """保存文本文件"""
        async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
            await f.write(text)
    
    def extract_text_from_row(self, row) -> str:
        """从表格行提取文本"""
        cells = row.find_all("td")
        candidates = []
        for cell in cells:
            if cell.find("div", class_="bikited-audio") or cell.find(
                "div", class_="bikit-audio"
            ):
                continue
            text = cell.get_text(strip=True)
            if text:
                candidates.append(text)
        if not candidates:
            return "no_text"
        return max(candidates, key=len)
    
    def extract_texts_from_container(self, container) -> Dict[str, str]:
        """从容器提取多语言文本"""
        texts: Dict[str, str] = {}
        
        # 提取日文
        jp_node = container.find(class_="voice_text_jp")
        if jp_node:
            text = jp_node.get_text(strip=True)
            if text:
                texts["jp"] = text
        
        # 提取中文简体
        chs_node = container.find(class_="voice_text_chs")
        if chs_node:
            text = chs_node.get_text(strip=True)
            if text:
                texts["zh"] = text
        
        # 提取中文繁体作为备选
        if "zh" not in texts:
            cht_node = container.find(class_="voice_text_cht")
            if cht_node:
                text = cht_node.get_text(strip=True)
                if text:
                    texts["zh"] = text
        
        if texts:
            return texts
        
        # 如果没有找到特定class，尝试提取最长的文本
        candidates = []
        for child in container.find_all(["td", "div"], recursive=False):
            if child.find("div", class_="bikited-audio") or child.find(
                "div", class_="bikit-audio"
            ):
                continue
            text = child.get_text(strip=True)
            if text:
                candidates.append(text)
        
        if candidates:
            texts["jp"] = max(candidates, key=len)
        
        return texts
    
    def extract_audio_url(self, node) -> Optional[str]:
        """从节点提取音频URL"""
        for key in ("data-src", "data-url", "data-file", "data-audio", "src"):
            value = node.get(key)
            if value and ".mp3" in value:
                return value
        
        # 遍历所有属性查找mp3
        for _, value in node.attrs.items():
            if isinstance(value, str) and ".mp3" in value:
                return value
        
        return None
    
    def extract_text_near_node(self, node) -> Dict[str, str]:
        """提取音频节点附近的文本"""
        # 向上查找父容器
        for parent in node.parents:
            if parent.name is None:
                continue
            texts = self.extract_texts_from_container(parent)
            if texts:
                return texts
        
        # 查找所在表格行
        row = node.find_parent("tr")
        if row:
            text = self.extract_text_from_row(row)
            return {"jp": text} if text != "no_text" else {}
        
        # 查找类似表格的div
        table_like = node.find_parent(
            lambda tag: tag.name == "div"
            and "display: table" in (tag.get("style") or "")
        )
        if table_like:
            return self.extract_texts_from_container(table_like)
        
        # 查找通用容器
        container = node.find_parent(["li", "div", "td"])
        if container:
            text = container.get_text(strip=True)
            if text:
                return {"jp": text}
        
        return {}
    
    async def download_character_audio(
        self,
        character_cn_name: str,
        character_en_name: str,
        dump_html: bool = False
    ) -> Dict:
        """
        下载单个角色的音频
        
        Args:
            character_cn_name: 角色中文名
            character_en_name: 角色英文名
            dump_html: 是否保存HTML
        
        Returns:
            下载结果字典 {
                'total': int,
                'success': int,
                'files': List[Dict]
            }
        """
        url = f"{BASE_URL}{character_cn_name}"
        
        async with AsyncWebCrawler(verbose=False) as crawler:
            result = await crawler.arun(url=url)
            
            if not result.success:
                logger.error(f"Failed to load {character_cn_name}: {url}")
                return {"total": 0, "success": 0, "files": []}
            
            soup = BeautifulSoup(result.html, "html.parser")
            
            # 保存HTML（调试用）
            if dump_html:
                html_dir = self.output_root / "html_dumps"
                html_dir.mkdir(exist_ok=True)
                html_path = html_dir / f"{character_en_name}.html"
                async with aiofiles.open(html_path, "w", encoding="utf-8") as f:
                    await f.write(result.html)
            
            # 创建角色目录
            save_dir = self.output_root / character_en_name
            save_dir.mkdir(exist_ok=True)
            
            # 查找所有音频节点
            audio_nodes = soup.select(
                "div.bikit-audio, div.bikited-audio, audio, source, "
                "[data-src], [data-url], [data-file], [data-audio]"
            )
            
            logger.info(f"Found {len(audio_nodes)} audio candidates for {character_cn_name}")
            
            # 提取音频URL和对应文本
            items: List[Tuple[str, Dict[str, str], str]] = []
            seen_urls = set()
            index = 0
            
            for node in audio_nodes:
                audio_url = self.extract_audio_url(node)
                if not audio_url:
                    continue
                
                if audio_url.startswith("//"):
                    audio_url = "https:" + audio_url
                
                if audio_url in seen_urls:
                    continue
                
                seen_urls.add(audio_url)
                index += 1
                
                texts = self.extract_text_near_node(node)
                file_basename = f"{character_en_name}_{index}"
                items.append((audio_url, texts, file_basename))
            
            if not items:
                logger.warning(f"No audio found for {character_cn_name}")
                return {"total": 0, "success": 0, "files": []}
            
            # 下载音频
            downloaded_files = []
            semaphore = asyncio.Semaphore(self.concurrency)
            limits = httpx.Limits(
                max_connections=self.concurrency,
                max_keepalive_connections=self.concurrency
            )
            
            async with httpx.AsyncClient(timeout=30.0, limits=limits) as client:
                pbar = tqdm(
                    total=len(items),
                    desc=f"{character_cn_name}",
                    unit="audio",
                    leave=False
                )
                
                async def handle_item(
                    audio_url: str,
                    texts: Dict[str, str],
                    file_basename: str
                ) -> Optional[Dict]:
                    mp3_path = save_dir / f"{file_basename}.mp3"
                    jp_path = save_dir / f"{file_basename}_jp.txt"
                    zh_path = save_dir / f"{file_basename}_zh.txt"
                    
                    try:
                        if not mp3_path.exists():
                            async with semaphore:
                                if self.request_delay:
                                    await asyncio.sleep(self.request_delay)
                                await self.download_file(client, audio_url, str(mp3_path))
                        
                        if texts.get("jp") and not jp_path.exists():
                            await self.save_text(texts["jp"], str(jp_path))
                        
                        if texts.get("zh") and not zh_path.exists():
                            await self.save_text(texts["zh"], str(zh_path))
                        
                        return {
                            "audio": str(mp3_path),
                            "text_jp": texts.get("jp", ""),
                            "text_zh": texts.get("zh", ""),
                            "url": audio_url
                        }
                    except Exception as e:
                        logger.warning(f"Failed to download {audio_url}: {e}")
                        return None
                    finally:
                        pbar.update(1)
                
                tasks = [handle_item(url, texts, name) for url, texts, name in items]
                results = await asyncio.gather(*tasks)
                pbar.close()
                
                downloaded_files = [r for r in results if r is not None]
            
            return {
                "total": len(items),
                "success": len(downloaded_files),
                "files": downloaded_files
            }
    
    async def batch_download(
        self,
        characters: Dict[str, str],
        page_delay: float = 0.5
    ) -> Dict[str, Dict]:
        """
        批量下载多个角色的音频
        
        Args:
            characters: {中文名: 英文名} 映射
            page_delay: 角色间延迟
        
        Returns:
            {角色名: 结果} 映射
        """
        results = {}
        
        for cn_name, en_name in characters.items():
            logger.info(f"Downloading audio for {cn_name} -> {en_name}")
            
            try:
                result = await self.download_character_audio(cn_name, en_name)
                results[cn_name] = result
                
                if page_delay:
                    await asyncio.sleep(page_delay)
            
            except Exception as e:
                logger.error(f"Failed to download {cn_name}: {e}")
                results[cn_name] = {"total": 0, "success": 0, "error": str(e)}
        
        return results


async def download_character_audio_simple(
    character_cn_name: str,
    character_en_name: str,
    output_dir: str = "./audio_downloads"
) -> Dict:
    """
    简化的单角色下载接口
    
    Args:
        character_cn_name: 角色中文名
        character_en_name: 角色英文名
        output_dir: 输出目录
    
    Returns:
        下载结果
    """
    downloader = AudioDownloader(output_root=output_dir)
    return await downloader.download_character_audio(
        character_cn_name,
        character_en_name
    )

