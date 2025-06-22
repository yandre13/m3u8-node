from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from playwright.async_api import async_playwright
import asyncio
import json
import re
import uvicorn
from typing import Optional, List, Dict, Any

app = FastAPI(title="pCloud M3U8 Extractor", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ExtractRequest(BaseModel):
    url: str

class ExtractResponse(BaseModel):
    success: bool
    m3u8_url: Optional[str] = None
    title: Optional[str] = None
    duration: Optional[float] = None
    thumbnail: Optional[str] = None
    quality: Optional[int] = None
    all_m3u8_urls: List[Dict[str, Any]] = []
    metadata: Dict[str, Any] = {}
    error: Optional[str] = None

class PCloudExtractor:
    def __init__(self):
        self.playwright = None
        self.browser = None

    async def init_browser(self):
        if not self.playwright:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-accelerated-2d-canvas',
                    '--no-first-run',
                    '--disable-gpu',
                    '--disable-background-timer-throttling',
                    '--disable-backgrounding-occluded-windows',
                    '--disable-renderer-backgrounding'
                ]
            )

    async def extract_m3u8(self, pcloud_url: str) -> ExtractResponse:
        await self.init_browser()
        page = await self.browser.new_page()

        try:
            m3u8_urls = []
            video_info = {}
            
            # Interceptar respuestas de red
            async def handle_response(response):
                url = response.url
                
                # Capturar M3U8
                if '.m3u8' in url:
                    m3u8_urls.append({
                        'url': url,
                        'status': response.status,
                        'headers': dict(response.headers)
                    })
                
                # Capturar metadata
                if ('getpublinkdownload' in url or 'api.pcloud.com' in url):
                    try:
                        if response.status == 200:
                            data = await response.json()
                            if 'metadata' in data:
                                video_info.update(data['metadata'])
                    except:
                        pass  # Ignorar errores de JSON
            
            page.on('response', handle_response)
            
            # Navegar a la página
            await page.goto(pcloud_url, wait_until='networkidle', timeout=30000)
            
            # Esperar a que aparezca el player
            try:
                await page.wait_for_selector(
                    'video, .video-player, [data-testid="video-player"]',
                    timeout=15000
                )
            except:
                pass  # Continuar aunque no encuentre el selector
            
            # Extraer información del DOM
            page_info = await page.evaluate("""
                () => {
                    const publinkData = window.publinkData;
                    const videoElement = document.querySelector('video');
                    
                    return {
                        publinkData: publinkData,
                        videoSrc: videoElement ? videoElement.src : null,
                        title: document.title,
                        metadata: window.fileMetadata || {}
                    };
                }
            """)
            
            # Procesar URLs M3U8 encontradas
            best_m3u8 = None
            if m3u8_urls:
                best_m3u8 = m3u8_urls[0]
            
            # Si no encontramos M3U8 en las requests, buscar en publinkData
            if not best_m3u8 and page_info.get('publinkData'):
                publink_data = page_info['publinkData']
                variants = publink_data.get('variants', [])
                
                for variant in variants:
                    if variant.get('transcodetype') == 'hls':
                        hosts = variant.get('hosts', [])
                        if hosts:
                            best_m3u8 = {
                                'url': f"https://{hosts[0]}{variant.get('path', '')}",
                                'quality': variant.get('height'),
                                'width': variant.get('width'),
                                'height': variant.get('height'),
                                'fps': variant.get('fps'),
                                'bitrate': variant.get('bitrate')
                            }
                            break
            
            # Construir respuesta
            publink_data = page_info.get('publinkData', {})
            
            return ExtractResponse(
                success=True,
                m3u8_url=best_m3u8.get('url') if best_m3u8 else None,
                title=publink_data.get('name') or page_info.get('title'),
                duration=publink_data.get('duration'),
                thumbnail=publink_data.get('thumb1024'),
                quality=best_m3u8.get('quality') if best_m3u8 else None,
                all_m3u8_urls=m3u8_urls,
                metadata=publink_data or page_info.get('metadata', {})
            )
            
        except Exception as error:
            return ExtractResponse(
                success=False,
                error=str(error)
            )
        finally:
            await page.close()

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None
            self.browser = None

# Instancia global del extractor
extractor = PCloudExtractor()

@app.post("/extract-pcloud", response_model=ExtractResponse)
async def extract_pcloud(request: ExtractRequest):
    if not request.url or 'pcloud.link' not in request.url:
        raise HTTPException(status_code=400, detail="Invalid pCloud URL")
    
    try:
        result = await extractor.extract_m3u8(request.url)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "OK", "service": "pCloud M3U8 Extractor"}

@app.on_event("shutdown")
async def shutdown_event():
    await extractor.close()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)