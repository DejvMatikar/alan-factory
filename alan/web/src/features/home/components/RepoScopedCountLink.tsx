import DescribedNavLink from "../../../components/DescribedNavLink";

export interface RepoScopedCountLinkProps {
  readonly label: string;
  readonly to: string;
  readonly count: number;
  readonly ariaLabel: string;
  readonly testId: string;
  readonly description: string;
}

export default function RepoScopedCountLink({
  label,
  to,
  count,
  ariaLabel,
  testId,
  description,
}: Readonly<RepoScopedCountLinkProps>) {
  return (
    <DescribedNavLink to={to} ariaLabel={ariaLabel} testId={testId} description={description} count={count}>
      {label}
    </DescribedNavLink>
  );
}
