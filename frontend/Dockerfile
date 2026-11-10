FROM node:24-alpine AS dependencies
WORKDIR /app
COPY package*.json ./
RUN npm ci
FROM dependencies AS development
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev"]
FROM dependencies AS build
ARG VITE_DATA_SOURCE=team
ENV VITE_DATA_SOURCE=$VITE_DATA_SOURCE
COPY . .
RUN npm run build
FROM nginx:stable-alpine AS production
ENV API_UPSTREAM=api:8000
ENV CHATBOT_ADMIN_TOKEN=
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY nginx-admin-routes.conf /etc/nginx/snippets/parking-admin-routes.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80 81
COPY maintenance.html /usr/share/nginx/html/maintenance.html
COPY maintenance-mascot.png /usr/share/nginx/html/assets/maintenance-mascot.png
