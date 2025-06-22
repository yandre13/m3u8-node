# Usar imagen base de Node.js con Puppeteer preinstalado
FROM ghcr.io/puppeteer/puppeteer:21.5.2

# Establecer directorio de trabajo
WORKDIR /usr/src/app

# Cambiar al usuario no root
USER pptruser

# Copiar package.json y package-lock.json
COPY --chown=pptruser:pptruser package*.json ./

# Instalar dependencias usando npm ci (más rápido y seguro)
RUN npm install --only=production && npm cache clean --force

# Copiar el código de la aplicación
COPY --chown=pptruser:pptruser . .

# Exponer el puerto
EXPOSE 3000

# Comando para ejecutar la aplicación
CMD ["node", "index.js"]