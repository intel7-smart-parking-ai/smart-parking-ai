import {useState} from 'react';

const pageSize = 10;

export function useTablePage(rows) {
  const [requestedPage, setPage] = useState(1);
  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
  const page = Math.min(requestedPage, totalPages);
  const start = (page - 1) * pageSize;
  return {rows: rows.slice(start, start + pageSize), page, totalPages, setPage};
}

export function TablePagination({label, pagination}) {
  const {page, totalPages, setPage} = pagination;
  if (totalPages <= 1) return null;

  return (
    <nav aria-label={label} style={{display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 10, padding: '10px 16px'}}>
      <button type="button" className="button white small admin-list-action" disabled={page === 1} onClick={() => setPage(page - 1)}>이전</button>
      <label>
        <select className="table-page-select" aria-label={label + ' 페이지 선택'} value={page} onChange={event => setPage(Number(event.target.value))}>
          {Array.from({length: totalPages}, (_, index) => index + 1).map(number => (
            <option key={number} value={number}>{number} / {totalPages} 페이지</option>
          ))}
        </select>
      </label>
      <button type="button" className="button white small admin-list-action" disabled={page === totalPages} onClick={() => setPage(page + 1)}>다음</button>
    </nav>
  );
}
