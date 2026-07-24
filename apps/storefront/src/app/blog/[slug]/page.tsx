import { notFound } from "next/navigation";
import Image from "next/image";
import Link from "next/link";
import TopBanner from "@/app/components/TopBanner";
import NavbarWrapper from "@/app/components/NavbarWrapper";
import Footer from "@/app/components/Footer";
import { getPost, getPosts, lexicalToText } from "@/lib/api";
import { isMediaDoc } from "@/types/cms";

export const revalidate = 60;

export async function generateStaticParams() {
  const posts = await getPosts({ publishedOnly: true, limit: 100 });
  return posts.map((p) => ({ slug: p.slug }));
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

export default async function BlogPostPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const post = await getPost(slug);

  if (!post || post.status === "DRAFT") notFound();

  const coverUrl = isMediaDoc(post.coverImage) && post.coverImage.url
    ? post.coverImage.url
    : "/images/blog-1.jpg";
  const bodyText = lexicalToText(post.body);

  return (
    <div className="min-h-screen">
      <TopBanner />
      <NavbarWrapper />

      {/* Hero image */}
      <div className="relative h-[300px] md:h-[460px] lg:h-[560px] overflow-hidden">
        <Image
          src={coverUrl}
          alt={post.title}
          fill
          className="object-cover"
          priority
        />
        <div className="absolute inset-0 bg-gradient-to-t from-black/50 to-transparent" />
        <div className="absolute bottom-0 inset-x-0 px-5 md:px-10 lg:px-20 pb-8 md:pb-12">
          <div className="max-w-3xl">
            <div className="flex items-center gap-3 text-sm text-white/70 mb-3">
              <span>{formatDate(post.publishedAt)}</span>
              {post.readTime && (
                <>
                  <span className="size-1.5 rounded-full bg-white/40" />
                  <span>{post.readTime}</span>
                </>
              )}
              {post.author && (
                <>
                  <span className="size-1.5 rounded-full bg-white/40" />
                  <span>By {post.author}</span>
                </>
              )}
            </div>
            <h1 className="text-2xl md:text-4xl lg:text-5xl font-semibold text-white leading-tight tracking-tight">
              {post.title}
            </h1>
          </div>
        </div>
      </div>

      {/* Content */}
      <article className="px-5 md:px-10 lg:px-20 py-10 md:py-14">
        <div className="max-w-3xl">
          {/* Breadcrumb */}
          <nav className="mb-8 text-sm text-neutral-400">
            <Link href="/" className="hover:text-neutral-600 transition-colors">
              Home
            </Link>
            <span className="mx-2">/</span>
            <Link href="/blog" className="hover:text-neutral-600 transition-colors">
              Blog
            </Link>
            <span className="mx-2">/</span>
            <span className="text-neutral-600">{post.title}</span>
          </nav>

          {post.excerpt && (
            <p className="text-lg md:text-xl text-neutral-500 leading-relaxed mb-8 font-medium">
              {post.excerpt}
            </p>
          )}

          {bodyText && (
            <div className="prose prose-neutral max-w-none text-neutral-600 leading-relaxed">
              <p className="whitespace-pre-line">{bodyText}</p>
            </div>
          )}

          {/* Back link */}
          <div className="mt-12 pt-8 border-t border-neutral-50">
            <Link
              href="/blog"
              className="inline-flex items-center gap-2 text-sm font-medium text-primary hover:text-primary-dark transition-colors"
            >
              <svg className="size-4" fill="none" viewBox="0 0 16 16">
                <path
                  d="M12.67 8H3.33M7.33 4l-4 4 4 4"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              Back to Blog
            </Link>
          </div>
        </div>
      </article>

      <Footer />
    </div>
  );
}
