import Link from "next/link";
import Image from "next/image";
import TopBanner from "../components/TopBanner";
import NavbarWrapper from "../components/NavbarWrapper";
import Footer from "../components/Footer";
import { getPosts } from "@/lib/api";
import type { Post } from "@/types/cms";
import { isMediaDoc } from "@/types/cms";

export const revalidate = 60;

export const metadata = {
  title: "Blog | Kelley Autoplex",
  description: "Automotive guides, tips, and insights to help you buy with confidence.",
};

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function coverUrl(post: Post): string {
  if (isMediaDoc(post.coverImage) && post.coverImage.url) return post.coverImage.url;
  return "/images/blog-1.jpg";
}

export default async function BlogPage() {
  const posts = await getPosts({ publishedOnly: true, limit: 20 });

  return (
    <div className="min-h-screen">
      <TopBanner />
      <NavbarWrapper />

      {/* Header */}
      <section className="bg-neutral-25 px-5 md:px-10 lg:px-20 py-10 md:py-14">
        <h1 className="text-3xl md:text-4xl lg:text-5xl font-semibold tracking-tight text-neutral-700">
          Blog &amp; Insights
        </h1>
        <p className="mt-2 md:mt-3 text-base md:text-lg text-neutral-500">
          Automotive guides, tips, and industry updates.
        </p>
      </section>

      {/* Posts grid */}
      <section className="px-5 md:px-10 lg:px-20 py-10 md:py-14">
        {posts.length === 0 ? (
          <div className="py-20 text-center">
            <p className="text-lg font-medium text-neutral-500">No posts published yet.</p>
            <p className="mt-2 text-sm text-neutral-400">Check back soon.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-8">
            {posts.map((post) => (
              <Link
                key={post.id}
                href={`/blog/${post.slug}`}
                className="group flex flex-col overflow-hidden rounded-2xl shadow-[0_0_8px_rgba(0,0,0,0.07)] hover:shadow-[0_0_16px_rgba(0,0,0,0.12)] transition-shadow"
              >
                <div className="relative h-[220px] overflow-hidden">
                  <Image
                    src={coverUrl(post)}
                    alt={post.title}
                    fill
                    className="object-cover group-hover:scale-105 transition-transform duration-300"
                  />
                </div>
                <div className="flex flex-col flex-1 p-5 md:p-6">
                  <div className="flex items-center gap-3 text-xs text-neutral-400">
                    <span>{formatDate(post.publishedAt)}</span>
                    {post.readTime && (
                      <>
                        <span className="size-1.5 rounded-full bg-neutral-200" />
                        <span>{post.readTime}</span>
                      </>
                    )}
                  </div>
                  <h2 className="mt-2 text-lg font-semibold text-neutral-700 leading-snug group-hover:text-primary transition-colors">
                    {post.title}
                  </h2>
                  {post.excerpt && (
                    <p className="mt-2 text-sm text-neutral-500 line-clamp-3 leading-relaxed">
                      {post.excerpt}
                    </p>
                  )}
                  <div className="mt-4 flex items-center gap-2 text-sm font-medium text-primary">
                    Read more
                    <svg className="size-4" fill="none" viewBox="0 0 16 16">
                      <path
                        d="M3.33 8h9.34M8.67 4l4 4-4 4"
                        stroke="currentColor"
                        strokeWidth="1.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </section>

      <Footer />
    </div>
  );
}
