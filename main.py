from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright
import asyncio
import json
import re
import uvicorn
import httpx
from typing import Optional, List, Dict, Any
import random
import time

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
    user_agent: Optional[str] = None
    proxy: Optional[str] = None
    use_residential_proxy: Optional[bool] = False

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
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        ]

    async def init_browser(self, proxy: Optional[str] = None, user_agent: Optional[str] = None):
        if not self.playwright:
            self.playwright = await async_playwright().start()
            
        browser_args = [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-accelerated-2d-canvas',
            '--no-first-run',
            '--disable-gpu',
            '--disable-background-timer-throttling',
            '--disable-backgrounding-occluded-windows',
            '--disable-renderer-backgrounding',
            '--disable-blink-features=AutomationControlled',
            '--disable-extensions',
            '--disable-plugins',
            '--disable-web-security',
            '--disable-features=VizDisplayCompositor'
        ]
        
        browser_options = {
            'headless': True,
            'args': browser_args
        }
        
        # Configurar proxy si se proporciona
        if proxy:
            browser_options['proxy'] = {'server': proxy}
            
        self.browser = await self.playwright.chromium.launch(**browser_options)

    async def get_random_user_agent(self):
        return random.choice(self.user_agents)

    async def extract_m3u8(self, pcloud_url: str, user_agent: Optional[str] = None, 
                          proxy: Optional[str] = None, client_ip: Optional[str] = None) -> ExtractResponse:
        
        # Reinicializar el navegador con nuevas configuraciones
        if self.browser:
            await self.browser.close()
            
        await self.init_browser(proxy=proxy, user_agent=user_agent)
        
        # Crear contexto del navegador con configuraciones específicas
        context_options = {
            'viewport': {'width': 1920, 'height': 1080},
            'user_agent': user_agent or await self.get_random_user_agent(),
            'java_script_enabled': True,
            'accept_downloads': False,
            'ignore_https_errors': True
        }
        
        # Agregar headers adicionales para simular un navegador real
        if client_ip:
            context_options['extra_http_headers'] = {
                'X-Forwarded-For': client_ip,
                'X-Real-IP': client_ip,
                'CF-Connecting-IP': client_ip
            }
        
        context = await self.browser.new_context(**context_options)
        
        # Inyectar scripts para evitar detección
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });
            
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
            });
            
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en'],
            });
            
            window.chrome = {
                runtime: {},
            };
        """)
        
        page = await context.new_page()

        try:
            m3u8_urls = []
            video_info = {}
            
            # Interceptar respuestas de red
            async def handle_response(response):
                url = response.url
                
                # Capturar M3U8
                if '.m3u8' in url:
                    try:
                        # Obtener headers de la respuesta
                        headers = dict(response.headers)
                        m3u8_urls.append({
                            'url': url,
                            'status': response.status,
                            'headers': headers,
                            'quality': self.extract_quality_from_url(url)
                        })
                    except Exception as e:
                        print(f"Error capturando M3U8: {e}")
                
                # Capturar metadata de la API
                if ('getpublinkdownload' in url or 'api.pcloud.com' in url or 
                    'getfilelink' in url or 'getvideolink' in url):
                    try:
                        if response.status == 200:
                            data = await response.json()
                            video_info.update(data)
                    except Exception as e:
                        print(f"Error capturando metadata: {e}")
            
            page.on('response', handle_response)
            
            # Configurar headers adicionales
            await page.set_extra_http_headers({
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none'
            })
            
            # Navegar con múltiples intentos
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    # Esperar un tiempo aleatorio entre intentos
                    if attempt > 0:
                        await asyncio.sleep(random.uniform(2, 5))
                    
                    await page.goto(pcloud_url, 
                                  wait_until='networkidle', 
                                  timeout=45000)
                    break
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise e
                    print(f"Intento {attempt + 1} fallido: {e}")
            
            # Esperar múltiples selectores posibles
            selectors_to_wait = [
                'video',
                '.video-player',
                '[data-testid="video-player"]',
                '.plyr',
                '.jwplayer',
                'video-js'
            ]
            
            for selector in selectors_to_wait:
                try:
                    await page.wait_for_selector(selector, timeout=10000)
                    break
                except:
                    continue
            
            # Esperar tiempo adicional para cargar completamente
            await asyncio.sleep(3)
            
            # Simular interacciones de usuario
            try:
                await page.mouse.move(100, 100)
                await page.mouse.click(100, 100)
                await asyncio.sleep(1)
            except:
                pass
            
            # Extraer información del DOM con múltiples estrategias
            page_info = await page.evaluate("""
                () => {
                    // Estrategia 1: publinkData
                    const publinkData = window.publinkData || window.pCloudData || {};
                    
                    // Estrategia 2: Buscar en variables globales
                    const globalData = {};
                    for (let key in window) {
                        if (key.toLowerCase().includes('video') || 
                            key.toLowerCase().includes('player') ||
                            key.toLowerCase().includes('stream')) {
                            try {
                                globalData[key] = window[key];
                            } catch(e) {}
                        }
                    }
                    
                    // Estrategia 3: Elementos de video
                    const videoElements = document.querySelectorAll('video');
                    const videoData = Array.from(videoElements).map(v => ({
                        src: v.src,
                        currentSrc: v.currentSrc,
                        poster: v.poster,
                        duration: v.duration
                    }));
                    
                    // Estrategia 4: Scripts con datos
                    const scripts = document.querySelectorAll('script');
                    let scriptData = {};
                    scripts.forEach(script => {
                        const content = script.textContent;
                        if (content && (content.includes('m3u8') || content.includes('.ts'))) {
                            const m3u8Match = content.match(/https?:[^"'\s]+\.m3u8[^"'\s]*/g);
                            if (m3u8Match) {
                                scriptData.m3u8_urls = m3u8Match;
                            }
                        }
                    });
                    
                    return {
                        publinkData: publinkData,
                        globalData: globalData,
                        videoData: videoData,
                        scriptData: scriptData,
                        title: document.title,
                        url: window.location.href
                    };
                }
            """)
            
            # Procesar URLs M3U8 encontradas
            best_m3u8 = self.select_best_m3u8(m3u8_urls)
            
            # Si no encontramos M3U8 en las requests, buscar en datos de página
            if not best_m3u8:
                best_m3u8 = await self.extract_m3u8_from_page_data(page_info)
            
            # Construir respuesta
            publink_data = page_info.get('publinkData', {})
            
            return ExtractResponse(
                success=True,
                m3u8_url=f"/proxy-m3u8?url={best_m3u8.get('url')}" if best_m3u8 else None,
                title=publink_data.get('name') or page_info.get('title'),
                duration=publink_data.get('duration'),
                thumbnail=publink_data.get('thumb1024'),
                quality=best_m3u8.get('quality') if best_m3u8 else None,
                all_m3u8_urls=m3u8_urls,
                metadata={
                    'publink_data': publink_data,
                    'video_info': video_info,
                    'page_info': page_info
                }
            )
            
        except Exception as error:
            return ExtractResponse(
                success=False,
                error=str(error)
            )
        finally:
            await context.close()

    def extract_quality_from_url(self, url: str) -> Optional[int]:
        """Extraer calidad del URL M3U8"""
        quality_patterns = [
            r'(\d+)p',
            r'_(\d+)_',
            r'quality_(\d+)',
            r'res_(\d+)'
        ]
        
        for pattern in quality_patterns:
            match = re.search(pattern, url)
            if match:
                return int(match.group(1))
        return None

    def select_best_m3u8(self, m3u8_urls: List[Dict]) -> Optional[Dict]:
        """Seleccionar la mejor URL M3U8"""
        if not m3u8_urls:
            return None
        
        # Filtrar URLs válidas
        valid_urls = [url for url in m3u8_urls if url.get('status') == 200]
        if not valid_urls:
            return m3u8_urls[0]  # Usar la primera si ninguna es válida
        
        # Preferir la de mayor calidad
        for url in sorted(valid_urls, key=lambda x: x.get('quality', 0), reverse=True):
            return url
        
        return valid_urls[0]

    async def extract_m3u8_from_page_data(self, page_info: Dict) -> Optional[Dict]:
        """Extraer M3U8 de los datos de la página"""
        publink_data = page_info.get('publinkData', {})
        
        # Buscar en variants
        variants = publink_data.get('variants', [])
        for variant in variants:
            if variant.get('transcodetype') == 'hls':
                hosts = variant.get('hosts', [])
                if hosts:
                    return {
                        'url': f"https://{hosts[0]}{variant.get('path', '')}",
                        'quality': variant.get('height'),
                        'width': variant.get('width'),
                        'height': variant.get('height'),
                        'fps': variant.get('fps'),
                        'bitrate': variant.get('bitrate')
                    }
        
        # Buscar en scriptData
        script_data = page_info.get('scriptData', {})
        m3u8_urls = script_data.get('m3u8_urls', [])
        if m3u8_urls:
            return {'url': m3u8_urls[0]}
        
        return None

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
async def extract_pcloud(request: ExtractRequest, client_request: Request):
    if not request.url or 'pcloud.link' not in request.url:
        raise HTTPException(status_code=400, detail="Invalid pCloud URL")
    
    # Obtener IP del cliente
    client_ip = client_request.headers.get("x-forwarded-for")
    if client_ip:
        client_ip = client_ip.split(",")[0].strip()
    else:
        client_ip = client_request.client.host
    
    try:
        result = await extractor.extract_m3u8(
            request.url,
            user_agent=request.user_agent,
            proxy=request.proxy,
            client_ip=client_ip
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/proxy-m3u8")
async def proxy_m3u8(url: str, request: Request):
    """Proxy para servir archivos M3U8 desde pCloud"""
    try:
        # Usar headers del cliente original
        headers = {
            'User-Agent': request.headers.get('user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'),
            'Accept': 'application/vnd.apple.mpegurl,video/mp2t,application/x-mpegURL,*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://pcloud.link/',
            'Origin': 'https://pcloud.link'
        }
        
        # Agregar IP del cliente si está disponible
        client_ip = request.headers.get("x-forwarded-for")
        if client_ip:
            client_ip = client_ip.split(",")[0].strip()
            headers['X-Forwarded-For'] = client_ip
            headers['X-Real-IP'] = client_ip
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                return StreamingResponse(
                    iter([response.content]),
                    media_type="application/vnd.apple.mpegurl",
                    headers={
                        "Access-Control-Allow-Origin": "*",
                        "Cache-Control": "no-cache",
                        "Access-Control-Allow-Headers": "*",
                        "Access-Control-Allow-Methods": "*"
                    }
                )
            else:
                raise HTTPException(status_code=response.status_code, detail="Failed to fetch M3U8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/stream/{path:path}")
async def stream_video(path: str, request: Request):
    """Proxy para segmentos de video"""
    try:
        segment_url = f"https://{path}"
        
        headers = {
            'User-Agent': request.headers.get('user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'),
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://pcloud.link/',
            'Range': request.headers.get('range', '')
        }
        
        # Agregar IP del cliente
        client_ip = request.headers.get("x-forwarded-for")
        if client_ip:
            client_ip = client_ip.split(",")[0].strip()
            headers['X-Forwarded-For'] = client_ip
            headers['X-Real-IP'] = client_ip
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(segment_url, headers=headers)
            if response.status_code in [200, 206]:
                response_headers = {
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "max-age=3600",
                    "Access-Control-Allow-Headers": "*",
                    "Access-Control-Allow-Methods": "*"
                }
                
                # Mantener headers de rango si existen
                if response.headers.get('content-range'):
                    response_headers['Content-Range'] = response.headers['content-range']
                if response.headers.get('accept-ranges'):
                    response_headers['Accept-Ranges'] = response.headers['accept-ranges']
                
                return StreamingResponse(
                    iter([response.content]),
                    media_type="video/mp2t",
                    status_code=response.status_code,
                    headers=response_headers
                )
            else:
                raise HTTPException(status_code=response.status_code, detail="Failed to fetch segment")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "OK", "service": "pCloud M3U8 Extractor Enhanced"}

@app.on_event("shutdown")
async def shutdown_event():
    await extractor.close()

if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)