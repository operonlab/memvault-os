import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

export default function InfoTip({ text }: { text: string }) {
  const [show, setShow] = useState(false)
  const btnRef = useRef<HTMLButtonElement>(null)
  const tipRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState({ top: 0, left: 0, maxWidth: 300 })

  const updatePos = useCallback(() => {
    if (!btnRef.current) return
    const rect = btnRef.current.getBoundingClientRect()
    const isMobile = window.innerWidth < 640

    if (isMobile) {
      // On mobile: center horizontally, appear above or below
      const tipWidth = Math.min(window.innerWidth - 24, 320)
      const left = Math.max(12, (window.innerWidth - tipWidth) / 2)
      // Prefer showing above the button if there's more space
      const spaceBelow = window.innerHeight - rect.bottom
      const top = spaceBelow > 200 ? rect.bottom + 8 : Math.max(8, rect.top - 8 - 200) // rough estimate
      setPos({ top, left, maxWidth: tipWidth })
    } else {
      // Desktop: show to the right, or left if overflow
      const tipWidth = 320
      let left = rect.right + 8
      if (left + tipWidth > window.innerWidth - 8) {
        left = rect.left - tipWidth - 8
      }
      if (left < 8) left = 8
      let top = rect.top
      if (top + 240 > window.innerHeight) {
        top = Math.max(8, window.innerHeight - 248)
      }
      setPos({ top, left, maxWidth: tipWidth })
    }
  }, [])

  useEffect(() => {
    if (!show) return
    updatePos()
    window.addEventListener('scroll', updatePos, true)
    window.addEventListener('resize', updatePos)
    return () => {
      window.removeEventListener('scroll', updatePos, true)
      window.removeEventListener('resize', updatePos)
    }
  }, [show, updatePos])

  return (
    <span className="inline-flex">
      <button
        ref={btnRef}
        onClick={(e) => {
          e.stopPropagation()
          setShow(!show)
        }}
        className="inline-flex items-center justify-center rounded-full text-xs leading-none transition-colors shrink-0"
        style={{
          backgroundColor: show ? 'var(--surface1)' : 'var(--surface0)',
          color: 'var(--subtext0)',
          minWidth: 20,
          minHeight: 20,
          width: 20,
          height: 20,
        }}
        title={text}
        aria-label="查看說明"
      >
        ?
      </button>
      {show &&
        createPortal(
          <>
            {/* Backdrop to close */}
            <div
              className="fixed inset-0"
              style={{ zIndex: 49998 }}
              onClick={(e) => {
                e.stopPropagation()
                setShow(false)
              }}
            />
            {/* Tooltip */}
            <div
              ref={tipRef}
              className="fixed max-h-[calc(100vh-32px)] overflow-y-auto rounded-xl border px-4 py-3 text-xs leading-relaxed shadow-xl"
              style={{
                zIndex: 49999,
                top: pos.top,
                left: pos.left,
                maxWidth: pos.maxWidth,
                whiteSpace: 'pre-line',
                backgroundColor: 'var(--mantle)',
                borderColor: 'var(--surface1)',
                color: 'var(--subtext1)',
              }}
            >
              {text}
            </div>
          </>,
          document.body,
        )}
    </span>
  )
}
