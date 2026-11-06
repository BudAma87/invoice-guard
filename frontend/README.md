# Frontend (Next.js review UI)

Create the app once (from the repo root):

```bash
npx create-next-app@latest frontend --ts --tailwind --eslint --app --src-dir --import-alias "@/*"
cd frontend && npx shadcn@latest init
npm i @tanstack/react-query @tanstack/react-table react-pdf
```

Screens (see TSD §7): Review queue · Review workspace (PDF + Date / Total / Line-items cards) · Checker view · Ops dashboard.
CI picks up the frontend job automatically once `frontend/package.json` exists.
