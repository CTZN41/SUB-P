import { Link } from 'react-router-dom'

type PlaceholderPageProps = {
  eyebrow: string
  title: string
  description: string
}

export function PlaceholderPage({ eyebrow, title, description }: PlaceholderPageProps) {
  return (
    <div className="page placeholder-page">
      <section className="placeholder-card">
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p>{description}</p>
        <div className="placeholder-status">화면 이동 준비 완료 · 실제 기능 미연결</div>
        <Link className="button secondary" to="/">홈으로 돌아가기</Link>
      </section>
    </div>
  )
}
