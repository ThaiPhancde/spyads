FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG API_URL
ENV API_URL=${API_URL}
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
# runtime needs these too (SSE route + middleware read them at request time); `docker run -e` overrides
ARG API_URL
ARG ADMIN_TOKEN
ENV API_URL=${API_URL} ADMIN_TOKEN=${ADMIN_TOKEN}
COPY --from=build /app/.next ./.next
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/package.json ./package.json
COPY --from=build /app/next.config.js ./next.config.js
COPY --from=build /app/middleware.js ./middleware.js
EXPOSE 3000
CMD ["sh", "-c", "npx next start -p ${PORT:-3000}"]
