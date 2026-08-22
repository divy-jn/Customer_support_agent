# Project Bestie — Frontend

This is the Next.js 16 frontend for the Project Bestie platform. It provides the customer chat interface, agent co-pilot dashboard, and tech admin settings.

## Getting Started

First, install dependencies and run the development server:

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

## Routes

- `/` or `/chat`: Main Customer Chat interface
- `/agent`: Agent Live Co-Pilot dashboard
- `/agent/sessions`: Live session monitoring
- `/agent/tickets`: Ticket management
- `/agent/customers`: Customer database
- `/agent/products`: Product catalog
- `/admin`: System Admin dashboard
- `/admin/settings`: Real-time model configuration
- `/admin/logs`: Application logs
- `/admin/knowledge`: RAG Knowledge Base management

## Environment Configuration

By default, the frontend connects to the backend at `http://localhost:8000` and `ws://localhost:8000`.

To override these, create a `.env.local` file:

```ini
NEXT_PUBLIC_API_URL=https://api.yourdomain.com
NEXT_PUBLIC_WS_URL=wss://api.yourdomain.com
```

## Development Commands

- `npm run dev`: Start the development server
- `npm run lint`: Run ESLint checks
- `npm run build`: Create an optimized production build
