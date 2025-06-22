const express = require('express')
const puppeteer = require('puppeteer')
const cors = require('cors')

const app = express()
app.use(cors())
app.use(express.json())

class PCloudExtractor {
  constructor() {
    this.browser = null
  }

  async initBrowser() {
    if (!this.browser) {
      this.browser = await puppeteer.launch({
        headless: true,
        args: [
          '--no-sandbox',
          '--disable-setuid-sandbox',
          '--disable-dev-shm-usage',
          '--disable-accelerated-2d-canvas',
          '--no-first-run',
          '--no-zygote',
          '--disable-gpu',
        ],
      })
    }
  }

  async extractM3U8(pcloudUrl) {
    await this.initBrowser()
    const page = await this.browser.newPage()

    try {
      // Interceptar requests de red
      await page.setRequestInterception(true)
      let m3u8Urls = []
      let videoInfo = {}

      page.on('request', (request) => {
        request.continue()
      })

      page.on('response', async (response) => {
        const url = response.url()

        // Capturar M3U8
        if (url.includes('.m3u8')) {
          m3u8Urls.push({
            url: url,
            status: response.status(),
            headers: response.headers(),
          })
        }

        // Capturar metadata
        if (
          url.includes('getpublinkdownload') ||
          url.includes('api.pcloud.com')
        ) {
          try {
            const data = await response.json()
            if (data.metadata) {
              videoInfo = data.metadata
            }
          } catch (e) {
            // Ignorar errores de JSON
          }
        }
      })

      // Navegar a la página
      await page.goto(pcloudUrl, {
        waitUntil: 'networkidle2',
        timeout: 30000,
      })

      // Esperar a que aparezca el player
      await page.waitForSelector(
        'video, .video-player, [data-testid="video-player"]',
        {
          timeout: 15000,
        }
      )

      // Extraer información del DOM
      const pageInfo = await page.evaluate(() => {
        // Buscar datos en window
        const publinkData = window.publinkData
        const videoElement = document.querySelector('video')

        return {
          publinkData: publinkData,
          videoSrc: videoElement ? videoElement.src : null,
          title: document.title,
          metadata: window.fileMetadata || {},
        }
      })

      // Procesar URLs M3U8 encontradas
      let bestM3U8 = null
      if (m3u8Urls.length > 0) {
        bestM3U8 = m3u8Urls[0]
      }

      // Si no encontramos M3U8 en las requests, buscar en publinkData
      if (!bestM3U8 && pageInfo.publinkData) {
        const variants = pageInfo.publinkData.variants || []
        for (const variant of variants) {
          if (variant.transcodetype === 'hls') {
            const hosts = variant.hosts || []
            if (hosts.length > 0) {
              bestM3U8 = {
                url: `https://${hosts[0]}${variant.path}`,
                quality: variant.height,
                width: variant.width,
                height: variant.height,
                fps: variant.fps,
                bitrate: variant.bitrate,
              }
              break
            }
          }
        }
      }

      return {
        success: true,
        m3u8_url: bestM3U8?.url,
        title: pageInfo.publinkData?.name || pageInfo.title,
        duration: pageInfo.publinkData?.duration,
        thumbnail: pageInfo.publinkData?.thumb1024,
        quality: bestM3U8?.quality,
        all_m3u8_urls: m3u8Urls,
        metadata: pageInfo.publinkData || pageInfo.metadata,
      }
    } catch (error) {
      return {
        success: false,
        error: error.message,
      }
    } finally {
      await page.close()
    }
  }

  async close() {
    if (this.browser) {
      await this.browser.close()
      this.browser = null
    }
  }
}

const extractor = new PCloudExtractor()

app.post('/extract-pcloud', async (req, res) => {
  try {
    const { url } = req.body

    if (!url || !url.includes('pcloud.link')) {
      return res.status(400).json({ error: 'Invalid pCloud URL' })
    }

    const result = await extractor.extractM3U8(url)
    res.json(result)
  } catch (error) {
    res.status(500).json({ error: error.message })
  }
})

app.get('/health', (req, res) => {
  res.json({ status: 'OK', service: 'pCloud M3U8 Extractor' })
})

// Cerrar browser al terminar
process.on('SIGINT', async () => {
  await extractor.close()
  process.exit(0)
})

const PORT = process.env.PORT || 3000
app.listen(PORT, () => {
  console.log(`🚀 pCloud Extractor running on port ${PORT}`)
})
